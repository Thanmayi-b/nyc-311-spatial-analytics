# NYC 311 Spatial Response Analytics

An end-to-end analytics project examining how long NYC 311 service requests take to close, by ZIP code. Raw records are pulled from a public REST API, aggregated with **DuckDB SQL**, joined to ZIP polygons in **GeoPandas**, and rendered as an interactive **Plotly** choropleth.

## Technical Stack
- **Languages:** Python 3.10+, SQL
- **SQL engine:** DuckDB (CTEs, timestamp math, aggregates, HAVING)
- **Geospatial:** GeoPandas, Shapely, pyogrio
- **Visualization:** Plotly Express (`choropleth_map`, Carto-Positron basemap, no token needed)
- **Ingestion:** Requests, Pandas
- **Data source:** NYC Open Data 311 Service Requests (Socrata API)

## Pipeline
```text
[NYC Open Data API] --(Requests, paginated)--> [data/raw/311_raw_sample.csv]
                                                      |
                                              (DuckDB SQL aggregation)
                                                      v
                                        [data/processed/zip_metrics_summary.csv]
                                                      |
[ZIP polygon GeoJSON] --(GeoPandas)--> merge on ZIP code --> [Plotly choropleth HTML]
```

## Setup & Run
```bash
pip install -r requirements.txt
python spatial_choropleth.py          # writes nyc_311_spatial_map.html
python spatial_choropleth.py --show   # also opens it in a browser
```
Optional: set `SOCRATA_APP_TOKEN` to avoid API throttling.

## Method Notes & Limitations
- Sample: closed requests created in a fixed window (Jan 2024), capped at 200k rows.
- Only **closed** tickets are included, which biases averages toward faster resolutions.
- ZIPs with fewer than 10 closed tickets are excluded.
- Map color scale is capped at the 95th percentile so outliers don't wash out the map.
- ZIP polygons are lightly simplified for file size.
