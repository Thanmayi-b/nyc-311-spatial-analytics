# NYC 311 Spatial Response Analytics

An end-to-end geospatial analytics project investigating municipal service response times across New York City ZIP codes. Raw public data is fetched via REST API, queried and aggregated using **DuckDB (Spatial SQL)**, processed in **GeoPandas**, and rendered in an interactive **Plotly Mapbox** choropleth dashboard.

---

## Technical Stack
- **Languages:** Python 3.10+, SQL
- **Database / SQL Engine:** DuckDB
- **Geospatial Processing:** GeoPandas, Shapely
- **Visualization:** Plotly Express (Mapbox Carto-Positron)
- **Data Ingestion:** Requests, Pandas
- **Data Endpoint:** NYC Open Data 311 REST API

---

## Architecture Pipeline
```text
[NYC Open Data API] ──(Requests)──> [data/raw/311_raw_sample.csv]
                                            │
                                    (DuckDB SQL Query)
                                            │
                                            ▼
[GeoJSON Polygon Boundaries] ──> [GeoPandas Spatial Join] ──> [Plotly Interactive Map]