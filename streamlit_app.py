import streamlit as st
import requests
import pandas as pd
import time
import feedparser
from datetime import datetime
import xml.etree.ElementTree as ET
import urllib.parse

# --- CONFIG ---
RATE_LIMIT_DELAY = 0.4

st.set_page_config(page_title="PubMed Smart Fetcher", layout="wide")
st.title("🔬 PubMed Smart Fetcher (RSS + API Hybrid)")

# --- INPUTS ---
keywords = st.text_input("Enter keywords (e.g., cancer immunotherapy)")
start_date = st.date_input("Start date", datetime(2023, 1, 1))
end_date = st.date_input("End date", datetime.today())
max_results = st.slider("Max results", 10, 100, 30)
fetch_full = st.checkbox("Fetch full abstracts (uses API)", value=False)

# --- ✅ FIXED RSS BUILDER ---
def build_rss_url(query, start_date, end_date, max_results):
    base = "https://pubmed.ncbi.nlm.nih.gov/rss/search/"

    formatted_query = (
        f"({query}[Title/Abstract]) AND "
        f"({start_date.strftime('%Y/%m/%d')}[dp] : {end_date.strftime('%Y/%m/%d')}[dp])"
    )

    encoded_query = urllib.parse.quote(formatted_query)

    return f"{base}?term={encoded_query}&size={max_results}"

# --- SAFE REQUEST ---
def safe_request(url, params=None, retries=3):
    for attempt in range(retries):
        try:
            time.sleep(RATE_LIMIT_DELAY)
            response = requests.get(url, params=params, timeout=20)

            if response.status_code == 200:
                return response

            if response.status_code in [429, 500, 502, 503]:
                time.sleep(2 ** attempt)

        except requests.exceptions.RequestException:
            time.sleep(2 ** attempt)

    return None

# --- RSS FETCH ---
@st.cache_data(ttl=3600)
def fetch_rss_data(rss_url):
    feed = feedparser.parse(rss_url)

    articles = []
    ids = []

    for entry in feed.entries:
        link = entry.link
        pmid = link.rstrip("/").split("/")[-1]

        ids.append(pmid)

        articles.append({
            "Title": entry.title,
            "Published": getattr(entry, "published", "N/A"),
            "Summary": entry.summary,
            "PMID": pmid,
            "Link": link
        })

    return pd.DataFrame(articles), ids

# --- ✅ FALLBACK API SEARCH ---
@st.cache_data(ttl=3600)
def fallback_search(query, start_date, end_date, max_results):
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"

    params = {
        "db": "pubmed",
        "term": f"{query}[Title/Abstract]",
        "retmax": max_results,
        "retmode": "json",
        "mindate": start_date.strftime("%Y/%m/%d"),
        "maxdate": end_date.strftime("%Y/%m/%d"),
        "datetype": "pdat"
    }

    response = safe_request(url, params)
    if not response:
        return []

    data = response.json()
    return data.get("esearchresult", {}).get("idlist", [])

# --- SAFE XML FETCH ---
@st.cache_data(ttl=3600)
def fetch_full_details(id_list):
    if not id_list:
        return pd.DataFrame()

    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

    params = {
        "db": "pubmed",
        "id": ",".join(id_list),
        "retmode": "xml"
    }

    response = safe_request(url, params)
    if not response:
        return pd.DataFrame()

    try:
        root = ET.fromstring(response.text)
    except ET.ParseError:
        return pd.DataFrame()

    articles = []

    for article in root.findall(".//PubmedArticle"):
        try:
            pmid = article.findtext(".//PMID", default="")

            abstract_parts = [
                elem.text or "" for elem in article.findall(".//AbstractText")
            ]
            abstract = " ".join(abstract_parts)

            articles.append({
                "PMID": pmid,
                "Abstract": abstract
            })

        except Exception:
            continue

    return pd.DataFrame(articles)

# --- MAIN ---
if st.button("Search"):
    if not keywords:
        st.warning("Please enter keywords.")
        st.stop()

    with st.spinner("Fetching via RSS..."):
        rss_url = build_rss_url(keywords, start_date, end_date, max_results)

        # ✅ OPTIONAL DEBUG (very useful on Streamlit Cloud)
        st.write("RSS URL:", rss_url)

        df, ids = fetch_rss_data(rss_url)

    # --- ✅ FALLBACK LOGIC ---
    if df.empty or not ids:
        st.warning("RSS returned no results — falling back to API search...")

        ids = fallback_search(keywords, start_date, end_date, max_results)

        if not ids:
            st.error("No results found.")
            st.stop()

        # Create minimal dataframe from IDs
        df = pd.DataFrame({"PMID": ids})

    st.success(f"Found {len(df)} articles")

    # --- OPTIONAL FULL ABSTRACT FETCH ---
    if fetch_full:
        with st.spinner("Fetching full abstracts (API)..."):
            details_df = fetch_full_details(ids)

        if not details_df.empty:
            df = df.merge(details_df, on="PMID", how="left")

    st.dataframe(df, use_container_width=True)

    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("Download CSV", csv, "pubmed_results.csv", "text/csv")