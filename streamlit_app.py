import streamlit as st
import requests
import pandas as pd
import time
import feedparser
from datetime import datetime
import xml.etree.ElementTree as ET

# --- CONFIG ---
RATE_LIMIT_DELAY = 0.4  # safe without API key

st.set_page_config(page_title="PubMed Smart Fetcher", layout="wide")
st.title("🔬 PubMed Smart Fetcher (RSS + API Hybrid)")

# --- INPUTS ---
keywords = st.text_input("Enter keywords")
start_date = st.date_input("Start date", datetime(2023, 1, 1))
end_date = st.date_input("End date", datetime.today())
max_results = st.slider("Max results", 10, 100, 30)
fetch_full = st.checkbox("Fetch full abstracts (uses API)", value=False)

# --- BUILD RSS URL ---
def build_rss_url(query, start_date, end_date, max_results):
    base = "https://pubmed.ncbi.nlm.nih.gov/rss/search/"

    formatted_query = f"{query} AND ({start_date.strftime('%Y/%m/%d')}:{end_date.strftime('%Y/%m/%d')}[dp])"

    return f"{base}?term={formatted_query}&size={max_results}"

# --- SAFE REQUEST ---
def safe_request(url, params=None, retries=3):
    for attempt in range(retries):
        try:
            time.sleep(RATE_LIMIT_DELAY)
            response = requests.get(url, params=params, timeout=10)

            if response.status_code == 200:
                return response

            if response.status_code in [429, 500, 502, 503]:
                time.sleep(2 ** attempt)

        except requests.exceptions.RequestException:
            time.sleep(2 ** attempt)

    return None

# --- RSS FETCH (CACHED) ---
@st.cache_data(ttl=3600)
def fetch_rss_data(rss_url):
    feed = feedparser.parse(rss_url)

    articles = []
    ids = []

    for entry in feed.entries:
        # Extract PubMed ID from link
        link = entry.link
        pmid = link.rstrip("/").split("/")[-1]

        ids.append(pmid)

        articles.append({
            "Title": entry.title,
            "Published": entry.published,
            "Summary": entry.summary,
            "PMID": pmid,
            "Link": link
        })

    return pd.DataFrame(articles), ids

# --- API FETCH (CACHED) ---
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

    root = ET.fromstring(response.text)
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

# --- SPAM PROTECTION ---
if "last_run" not in st.session_state:
    st.session_state.last_run = 0

if st.button("Search"):
    now = time.time()

    if now - st.session_state.last_run < 2:
        st.warning("Please wait before searching again.")
    elif not keywords:
        st.warning("Enter keywords.")
    else:
        st.session_state.last_run = now

        with st.spinner("Fetching via RSS (fast & efficient)..."):
            rss_url = build_rss_url(keywords, start_date, end_date, max_results)
            df, ids = fetch_rss_data(rss_url)

        if df.empty:
            st.warning("No results found.")
        else:
            st.success(f"Found {len(df)} articles (RSS)")

            # --- OPTIONAL API ENRICHMENT ---
            if fetch_full:
                with st.spinner("Fetching full abstracts (1 API call)..."):
                    details_df = fetch_full_details(ids)

                if not details_df.empty:
                    df = df.merge(details_df, on="PMID", how="left")

            st.dataframe(df, use_container_width=True)

            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button("Download CSV", csv, "pubmed_results.csv", "text/csv")