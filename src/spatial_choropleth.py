import argparse
import json
import os
import sys
import traceback
from pathlib import Path

import duckdb
import geopandas as gpd
import pandas as pd
import plotly.express as px
import requests

SOCRATA_URL = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
GEOJSON_URL = (
    "https://raw.githubusercontent.com/nycehs/NYC_geography/master/MODZCTA_2010_WGS1984.geo.json"
)

# Fixed date window so the sample is reproducible and not biased toward arbitrary rows.
START_DATE = "2024-01-01T00:00:00"
END_DATE = "2024-02-01T00:00:00"
PAGE_SIZE = 5_000
MAX_ROWS = 100_000

RAW_PATH = Path("data/raw/311_raw_sample.csv")
SUMMARY_PATH = Path("data/processed/zip_metrics_summary.csv")
OUTPUT_HTML = "nyc_311_spatial_map.html"


def fetch_raw_data():
    """
    Extract closed 311 requests for a fixed date window from the NYC Open Data
    (Socrata) API, paginating with a stable sort, and save them to CSV.
    """
    print("[1/4] Fetching raw 311 Service Requests from NYC Open Data API...")

    headers = {}
    token = os.environ.get("SOCRATA_APP_TOKEN")  # optional, avoids throttling
    if token:
        headers["X-App-Token"] = token

    where = (
        f"created_date >= '{START_DATE}' AND created_date < '{END_DATE}' "
        "AND incident_zip IS NOT NULL AND closed_date IS NOT NULL"
    )

    frames, offset = [], 0
    while offset < MAX_ROWS:
        params = {
            "$select": "incident_zip,complaint_type,created_date,closed_date",
            "$where": where,
            "$order": ":id",  # stable ordering so pages don't overlap
            "$limit": PAGE_SIZE,
            "$offset": offset,
        }
        response = requests.get(SOCRATA_URL, params=params, headers=headers, timeout=180)
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        frames.append(pd.DataFrame(batch))
        offset += PAGE_SIZE
        if len(batch) < PAGE_SIZE:
            break

    if not frames:
        raise RuntimeError("API returned no rows for the requested window.")

    df_raw = pd.concat(frames, ignore_index=True)
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    df_raw.to_csv(RAW_PATH, index=False)
    print(f"      Saved {len(df_raw):,} rows to {RAW_PATH}")


def process_with_sql():
    """
    Use DuckDB to aggregate resolution times per ZIP code (CTEs, timestamp math,
    aggregates, HAVING filter).
    """
    print("[2/4] Running SQL transformations & aggregation in DuckDB...")

    sql_query = f"""
    WITH parsed_data AS (
        SELECT
            LEFT(incident_zip, 5) AS ZIPCODE,
            complaint_type,
            TRY_CAST(created_date AS TIMESTAMP) AS created_ts,
            TRY_CAST(closed_date AS TIMESTAMP) AS closed_ts
        FROM read_csv('{RAW_PATH.as_posix()}', header = true, all_varchar = true)
        WHERE regexp_matches(incident_zip, '^[0-9]{{5}}')
          AND closed_date IS NOT NULL
    ),
    duration_calculated AS (
        SELECT
            ZIPCODE,
            complaint_type,
            EPOCH(closed_ts - created_ts) / 3600.0 AS resolution_hours
        FROM parsed_data
        WHERE created_ts IS NOT NULL
          AND closed_ts IS NOT NULL
          AND closed_ts >= created_ts
    )
    SELECT
        ZIPCODE,
        COUNT(*) AS total_requests,
        ROUND(AVG(resolution_hours), 1) AS avg_resolution_hours,
        ROUND(MEDIAN(resolution_hours), 1) AS median_resolution_hours
    FROM duration_calculated
    GROUP BY ZIPCODE
    HAVING COUNT(*) >= 10
    ORDER BY total_requests DESC;
    """

    con = duckdb.connect()
    try:
        df_metrics = con.execute(sql_query).df()
    finally:
        con.close()

    if df_metrics.empty:
        raise RuntimeError("SQL aggregation returned no ZIP codes (try a larger window).")

    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    df_metrics.to_csv(SUMMARY_PATH, index=False)
    print(f"      Saved SQL query output to {SUMMARY_PATH} ({len(df_metrics)} ZIP codes)")
    return df_metrics


def fetch_geojson_boundaries():
    """
    Load NYC ZIP polygons into a GeoDataFrame, standardize to EPSG:4326,
    and dissolve duplicate ZIP features into one geometry per ZIP.
    """
    print("[3/4] Loading NYC ZIP Code GeoJSON polygon boundaries...")

    geo_path = Path("data/zip_boundaries.geojson")
    if not geo_path.exists():
        response = requests.get(GEOJSON_URL, timeout=120)
        response.raise_for_status()
        geo_path.parent.mkdir(parents=True, exist_ok=True)
        geo_path.write_bytes(response.content)
    gdf_zip = gpd.read_file(geo_path)

    zip_col = next(
        (c for c in gdf_zip.columns if c.lower() in ("modzcta", "zipcode", "postalcode", "zcta5ce10", "zcta")),
        None,
    )
    if zip_col is None:
        raise RuntimeError(f"No ZIP column found. Columns are: {list(gdf_zip.columns)}")
    
    gdf_zip["ZIPCODE"] = gdf_zip[zip_col].astype(str).str.zfill(5)

    if gdf_zip.crs is None:
        gdf_zip = gdf_zip.set_crs(epsg=4326)  # GeoJSON default per spec
    elif gdf_zip.crs.to_epsg() != 4326:
        gdf_zip = gdf_zip.to_crs(epsg=4326)

    gdf_zip = gdf_zip[["ZIPCODE", "geometry"]].dissolve(by="ZIPCODE").reset_index()
    gdf_zip["geometry"] = gdf_zip.geometry.simplify(0.0005, preserve_topology=True)
    return gdf_zip


def generate_choropleth_map(gdf_zip, df_metrics, show=False):
    """
    Attribute-join SQL metrics onto ZIP polygons (on ZIPCODE) and build an
    interactive Plotly choropleth.
    """
    print("[4/4] Joining SQL metrics with ZIP polygons & building map...")

    gdf_merged = gdf_zip.merge(df_metrics, on="ZIPCODE", how="inner")
    if gdf_merged.empty:
        raise RuntimeError("No ZIP codes matched between metrics and polygons.")

    geojson_dict = json.loads(gdf_merged[["ZIPCODE", "geometry"]].to_json())
    df_plot = pd.DataFrame(gdf_merged.drop(columns="geometry"))

    fig = px.choropleth(
        df_plot,
        geojson=geojson_dict,
        locations="ZIPCODE",
        featureidkey="properties.ZIPCODE",
        color="avg_resolution_hours",
        color_continuous_scale="Viridis",
        range_color=(0, df_plot["avg_resolution_hours"].quantile(0.95)),
        labels={
            "avg_resolution_hours": "Avg Resolution (Hrs)",
            "median_resolution_hours": "Median Resolution (Hrs)",
            "total_requests": "Closed 311 Tickets",
            "ZIPCODE": "ZIP Code",
        },
        hover_data={
            "ZIPCODE": True,
            "total_requests": True,
            "avg_resolution_hours": True,
            "median_resolution_hours": True,
        },
        title=(
            "<b>NYC 311 Resolution Times by ZIP Code</b><br>"
            "<sup>Closed requests created Jan 2024; color scale capped at 95th percentile</sup>"
        ),
    )
    fig.update_geos(fitbounds="locations", visible=False)
    fig.update_traces(marker_line_width=0.5, marker_line_color="white")

    fig.update_layout(
        margin={"r": 0, "t": 60, "l": 0, "b": 0},
        font=dict(family="Arial, sans-serif", size=12),
        title_font_size=16,
    )

    fig.write_html(OUTPUT_HTML)
    print(f"\nPipeline completed! Exported HTML report to {OUTPUT_HTML}")
    if show:
        fig.show()


def main():
    parser = argparse.ArgumentParser(description="NYC 311 spatial response analytics")
    parser.add_argument("--show", action="store_true", help="open the map in a browser")
    args = parser.parse_args()

    try:
        if RAW_PATH.exists():
            print(f"[1/4] Using existing {RAW_PATH} (delete it to refetch)")
        else:
            fetch_raw_data()

        df_metrics = process_with_sql()
        gdf_zip = fetch_geojson_boundaries()
        generate_choropleth_map(gdf_zip, df_metrics, show=args.show)
    except Exception:
        print("\nPipeline execution failed:", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
