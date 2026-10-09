import streamlit as st
import pandas as pd
import requests
import folium
from streamlit_folium import st_folium
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

st.set_page_config(page_title="Pakistan Disaster Watch", page_icon="🇵🇰", layout="wide")

# Main UI language. Event/source names remain as supplied by the original feeds.
LANG = "English"  # Keep all non-map interface text in English.


st.markdown("""
<style>
.block-container{padding-top:1.25rem;max-width:1500px}
.hero{padding:22px 26px;border-radius:16px;background:linear-gradient(115deg,#0c392e,#16805a);color:white;margin-bottom:16px}
.hero h1{margin:0 0 5px 0;font-size:2rem}.hero p{margin:0;opacity:.9}
div[data-testid="stMetric"]{border:1px solid rgba(128,128,128,.25);padding:12px 15px;border-radius:12px}
</style>
<div class="hero"><h1>🇵🇰 Pakistan Disaster Watch</h1>
<p>Explore reported natural hazards in Pakistan on a simple, filterable map.</p></div>
""", unsafe_allow_html=True)

# Use Pakistan's actual international boundary, not a broad rectangle.
# This excludes events in Afghanistan, Iran, India, China, and other neighbors.
PAKISTAN_GEOJSON_URL = "https://raw.githubusercontent.com/datasets/geo-countries/master/data/countries.geojson"

@st.cache_data(ttl=86400, show_spinner=False)
def load_pakistan_boundary():
    from shapely.geometry import shape
    response = requests.get(PAKISTAN_GEOJSON_URL, timeout=25,
                            headers={"User-Agent": "PakistanDisasterWatch/1.0"})
    response.raise_for_status()
    geo = response.json()
    for feature in geo.get("features", []):
        props = feature.get("properties", {})
        country = str(props.get("ADMIN") or props.get("name") or props.get("NAME") or "").strip().lower()
        if country in {"pakistan", "islamic republic of pakistan"}:
            geom = shape(feature["geometry"])
            if not geom.is_empty and geom.is_valid:
                return geom
    raise RuntimeError("Could not locate Pakistan's official boundary in the boundary dataset.")

def in_pakistan(lat, lon):
    if lat is None or lon is None:
        return False
    try:
        from shapely.geometry import Point
        return load_pakistan_boundary().covers(Point(float(lon), float(lat)))
    except Exception:
        # Fail closed: never show an event if the true boundary cannot be checked.
        return False

CATEGORIES = [
    "Earthquakes", "Floods", "Severe storms", "Wildfires", "Landslides",
    "Drought", "Dust & haze", "Snow & ice", "Volcanoes", "Other natural events"
]

@st.cache_data(ttl=300, show_spinner=False)
def get_json(url, params=None):
    response = requests.get(url, params=params, timeout=25, headers={"User-Agent": "PakistanDisasterWatch/1.0"})
    response.raise_for_status()
    return response.json()

@st.cache_data(ttl=600, show_spinner=False)
def get_gdacs_rss():
    url = "https://www.gdacs.org/xml/rss.xml"
    response = requests.get(url, timeout=25, headers={"User-Agent": "PakistanDisasterWatch/1.0"})
    response.raise_for_status()
    root = ET.fromstring(response.content)
    out = []
    for item in root.findall(".//item"):
        title = item.findtext("title", default="Disaster alert")
        description = item.findtext("description", default="")
        link = item.findtext("link", default="https://www.gdacs.org/")
        published = item.findtext("pubDate", default="")
        lat = lon = None
        for node in item.iter():
            if node.tag.lower().endswith("point") and node.text:
                parts = node.text.strip().split()
                if len(parts) >= 2:
                    try:
                        lat, lon = float(parts[0]), float(parts[1])
                    except ValueError:
                        pass
        if in_pakistan(lat, lon):
            lower = title.lower()
            if "earthquake" in lower: category = "Earthquakes"
            elif "flood" in lower: category = "Floods"
            elif "cyclone" in lower or "storm" in lower: category = "Severe storms"
            elif "fire" in lower: category = "Wildfires"
            elif "landslide" in lower: category = "Landslides"
            elif "drought" in lower: category = "Drought"
            else: category = "Other natural events"
            out.append(dict(name=title, category=category, latitude=lat, longitude=lon,
                            date=published, source="GDACS", details=description, url=link))
    return out

def earthquake_rows(feed="all_day"):
    data = get_json(f"https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/{feed}.geojson")
    rows = []
    for f in data.get("features", []):
        p = f.get("properties", {})
        coords = (f.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 3:
            continue
        lon, lat, depth = coords[:3]
        if in_pakistan(lat, lon):
            mag = p.get("mag")
            rows.append(dict(
                name=p.get("place") or "Earthquake", category="Earthquakes",
                latitude=float(lat), longitude=float(lon),
                date=datetime.fromtimestamp((p.get("time") or 0)/1000, timezone.utc).isoformat(),
                source="USGS", details=f"Magnitude: {mag if mag is not None else 'not reported'} · Depth: {depth:g} km",
                url=p.get("url") or "https://earthquake.usgs.gov/"))
    return rows

def eonet_rows():
    data = get_json("https://eonet.gsfc.nasa.gov/api/v3/events", {"status":"open", "limit":500})
    mapping = {
        "floods":"Floods", "severe storms":"Severe storms", "wildfires":"Wildfires",
        "landslides":"Landslides", "drought":"Drought", "dust and haze":"Dust & haze",
        "snow":"Snow & ice", "sea and lake ice":"Snow & ice", "volcanoes":"Volcanoes"
    }
    rows = []
    for event in data.get("events", []):
        categories = event.get("categories", [])
        cat = mapping.get(categories[0].get("title", "").lower(), "Other natural events") if categories else "Other natural events"
        for g in event.get("geometry", []):
            coords = g.get("coordinates") or []
            if len(coords) < 2:
                continue
            # EONET point coordinates are [longitude, latitude].
            lon, lat = coords[:2]
            if in_pakistan(lat, lon):
                rows.append(dict(name=event.get("title","Natural event"), category=cat,
                    latitude=float(lat), longitude=float(lon), date=g.get("date",""),
                    source="NASA EONET", details=event.get("description") or "Open natural-event record",
                    url=event.get("link") or "https://eonet.gsfc.nasa.gov/"))
    return rows

with st.sidebar:
    st.subheader("Filters")
    selected_categories = st.multiselect("Disaster type", CATEGORIES, default=["Earthquakes","Floods","Severe storms","Landslides","Wildfires"])
    selected_sources = st.multiselect("Data source", ["USGS","NASA EONET","GDACS"], default=["USGS","NASA EONET","GDACS"])
    period = st.radio("Earthquake time range", ["Last 24 hours", "Last 7 days"], index=0)
    st.markdown("### Map settings")
    map_mode = st.radio("Map style", ["Roadmap", "Satellite"], horizontal=True)
    show_satellite_labels = st.checkbox(
        "Show city and area names on satellite", value=True
    )
    if st.button("↻ Refresh data now", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("Feeds are cached for about 5–10 minutes. Map labels and roads depend on the selected map layer.")
    st.divider()
    st.caption("A missing marker does not mean a location is safe. This is not an official warning service.")

earthquake_feed = "all_day" if period == "Last 24 hours" else "all_week"
rows, errors = [], []
if "USGS" in selected_sources:
    try: rows.extend(earthquake_rows(earthquake_feed))
    except Exception as e: errors.append(f"USGS earthquake feed: {e}")
if "NASA EONET" in selected_sources:
    try: rows.extend(eonet_rows())
    except Exception as e: errors.append(f"NASA EONET: {e}")
if "GDACS" in selected_sources:
    try: rows.extend(get_gdacs_rss())
    except Exception as e: errors.append(f"GDACS feed: {e}")

df = pd.DataFrame(rows, columns=["name","category","latitude","longitude","date","source","details","url"])
if not df.empty:
    df = df[df["category"].isin(selected_categories) & df["source"].isin(selected_sources)]
    df = df.drop_duplicates(subset=["name","category","latitude","longitude","source"])

if errors:
    with st.expander("Some feeds could not be reached", expanded=False):
        for err in errors: st.warning(err)

st.caption("Last checked: " + datetime.now().astimezone().strftime("%d %b %Y, %I:%M:%S %p %Z"))
a,b,c,d = st.columns(4)
a.metric("Events shown", len(df))
b.metric("Types represented", df["category"].nunique() if not df.empty else 0)
c.metric("Earthquakes", int((df["category"]=="Earthquakes").sum()) if not df.empty else 0)
d.metric("Sources responding", df["source"].nunique() if not df.empty else 0)

left, right = st.columns([1.7, 1], gap="large")
with left:
    st.subheader("Pakistan map")
    tiles = "OpenStreetMap" if map_mode == "Roadmap" else "Esri.WorldImagery"
    fmap = folium.Map(location=[30.4, 69.3], zoom_start=5, tiles=tiles, control_scale=True)
    # Fit the map to Pakistan's real boundary and draw the boundary outline.
    try:
        pakistan_boundary = load_pakistan_boundary()
        minx, miny, maxx, maxy = pakistan_boundary.bounds
        fmap.fit_bounds([[miny, minx], [maxy, maxx]], padding=(12, 12))
    except Exception as e:
        st.warning("Pakistan boundary could not be loaded. Events are filtered out unless the boundary is available.")
    # Satellite imagery has no place names by itself. This optional overlay adds useful
    # bilingual labels for major cities; it is separate from the disaster markers.
    if map_mode == "Satellite" and show_satellite_labels:
        cities = [
            (33.6844, 73.0479, "Islamabad"), (32.0837, 72.6710, "Sargodha"),
            (24.8607, 67.0011, "Karachi"), (34.0151, 71.5249, "Peshawar"),
            (30.1798, 66.9750, "Quetta"), (31.5497, 74.3436, "Lahore"),
            (32.1877, 74.1945, "Gujranwala"), (32.4945, 74.5229, "Sialkot"),
            (31.4504, 73.1350, "Faisalabad"), (30.1575, 71.5249, "Multan"),
            (27.7131, 68.8480, "Sukkur"), (25.3960, 68.3578, "Hyderabad"),
            (35.9208, 74.3089, "Gilgit"), (34.1688, 73.2215, "Abbottabad"),
            (33.6007, 73.0679, "Rawalpindi"), (29.3956, 71.6836, "Bahawalpur"),
            (28.4212, 70.2989, "Rahim Yar Khan"), (26.0150, 63.0470, "Gwadar / گوادر")
        ]
        label_group = folium.FeatureGroup(name="City and area labels", show=True)
        for lat, lon, label in cities:
            folium.Marker([lat, lon], icon=folium.DivIcon(html=f'<div style="font-size:11px;font-weight:700;color:white;text-shadow:-1px -1px 2px #000,1px 1px 2px #000;white-space:nowrap">{label}</div>')).add_to(label_group)
        label_group.add_to(fmap)
    if not df.empty:
        colors = {"Earthquakes":"red","Floods":"blue","Severe storms":"purple","Wildfires":"orange",
                  "Landslides":"darkred","Drought":"beige","Dust & haze":"gray","Snow & ice":"lightblue",
                  "Volcanoes":"darkpurple","Other natural events":"green"}
        for _, r in df.iterrows():
            popup_html = (
                f"<b>{r['name']}</b><br>Type: {r['category']}<br>Source: {r['source']}<br>"
                f"Date: {r['date']}<br>{r['details']}<br><a href='{r['url']}' target='_blank'>Open source record</a>"
            )
            folium.CircleMarker(
                location=[r["latitude"], r["longitude"]], radius=7,
                color=colors.get(r["category"], "red"), fill=True, fill_opacity=.8,
                tooltip=f"{r['category']}: {r['name']}", popup=folium.Popup(popup_html, max_width=320)
            ).add_to(fmap)
    st_folium(fmap, use_container_width=True, height=540, returned_objects=[])
    st.caption("Click a marker for its source, date, and event details. Satellite labels can be switched on or off in the sidebar.")
with right:
    st.subheader("Latest matching events")
    if df.empty:
        st.info("No matching records were returned by the selected feeds and filters. Try selecting more categories or sources.")
    else:
        for _, r in df.sort_values("date", ascending=False).head(12).iterrows():
            with st.container(border=True):
                st.markdown(f"**{r['name']}**")
                st.caption(f"{r['category']} · {r['source']}")
                if r["date"]: st.caption(str(r["date"]))
                st.write(str(r["details"])[:240])
                if isinstance(r["url"], str) and r["url"].startswith("http"):
                    st.markdown(f"[Open source record]({r['url']})")

st.divider()
st.subheader("Event records")
if df.empty:
    st.write("No records match these filters.")
else:
    table = df[["date","category","name","source","latitude","longitude","details","url"]].copy()
    st.dataframe(table, use_container_width=True, hide_index=True, column_config={
        "url":st.column_config.LinkColumn("Source"),
        "latitude":st.column_config.NumberColumn(format="%.3f"),
        "longitude":st.column_config.NumberColumn(format="%.3f")
    })
    st.download_button("Download filtered records (CSV)", table.to_csv(index=False).encode("utf-8"),
                       "pakistan_disaster_events.csv", "text/csv")

st.markdown("""
---
**Sources and limitations**
- [USGS earthquake feeds](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php): recent earthquakes, filtered to a broad Pakistan bounding box.
- [NASA EONET API](https://eonet.gsfc.nasa.gov/docs/v3): curated open natural-event records; it may not include every local incident.
- [GDACS feeds](https://data.gdacs.org/feed_reference.aspx): global disaster alerts, with available coordinates filtered to Pakistan.
- Some hazard categories may have few or no records because sources differ in coverage and update timing. This dashboard does not predict disasters or replace official alerts. For emergency decisions, follow Pakistan's official disaster-management authorities.
""")
