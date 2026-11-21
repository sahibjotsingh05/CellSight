"use strict";
function createFleetMap({ select, updateFocus }) {
  const canvasRegion = document.getElementById("map");
  const busy = () => canvasRegion.setAttribute("aria-busy", "true");
  const note = document.getElementById("map-note");
  const message = document.getElementById("map-message");
  const retry = document.getElementById("map-retry");
  let map, fleet = [], selected = null, mode = "satellite", baseLayers = [], fitted = false;
  const markers = new Map();
  const style = () => `https://tiles.openfreemap.org/styles/${document.documentElement.dataset.theme === "dark" ? "dark" : "positron"}`;
  function route() {
    const source = map?.getSource("selected-route");
    const bus = fleet.find(b => b.bus_id === selected);
    const path = bus?.route?.path || [];
    if (source) source.setData({type:"FeatureCollection", features:path.length > 1 ? [{type:"Feature", properties:{}, geometry:{type:"LineString", coordinates:path.map(([lat,lng]) => [lng,lat])}}] : []});
  }
  function applyMode() {
    busy();
    document.querySelectorAll("[data-map-mode]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.mapMode === mode)));
    if (!map?.getLayer("satellite-imagery")) return;
    baseLayers.forEach(({id, visibility}) => map.setLayoutProperty(id, "visibility", mode === "satellite" ? "none" : visibility));
    map.setLayoutProperty("satellite-imagery", "visibility", mode === "satellite" ? "visible" : "none");
  }
  try {
    map = new maplibregl.Map({container:"map", style:style(), center:[-113.4938,53.5461], zoom:11, renderWorldCopies:false, fadeDuration:0, attributionControl:false});
    // Data licences require visible credits; keep them behind a compact "i" button instead of a full text bar.
    const attribution = new maplibregl.AttributionControl({compact:true});
    map.addControl(attribution, "bottom-right");
    const collapseCredits = () => { const box = attribution._container; box?.classList.remove("maplibregl-compact-show"); box?.removeAttribute("open"); };
    map.addControl(new maplibregl.NavigationControl({showCompass:false}), "bottom-left");
    busy();
    map.on("idle", () => {
      if (map.isStyleLoaded() && (mode !== "satellite" || map.isSourceLoaded("satellite-imagery"))) canvasRegion.setAttribute("aria-busy", "false");
    });
    map.on("moveend", updateFocus);
    map.on("style.load", () => {
      const dark = document.documentElement.dataset.theme === "dark";
      if (dark) {
        const fills = {water:"#3b596f", landuse_residential:"#24262a", landcover_wood:"#2d4437", landuse_park:"#2d4437", building:"#2b2e33"};
        const roads = {highway_path:"#606771", highway_minor:"#707680", highway_major_casing:"#292c31", highway_major_inner:"#969ca5", highway_major_subtle:"#606771", highway_motorway_casing:"#292c31", highway_motorway_inner:"#b0b5bc", highway_motorway_subtle:"#707680", waterway:"#3b596f"};
        map.getStyle().layers.forEach(layer => {
          if (layer.type === "background") map.setPaintProperty(layer.id,"background-color","#24262a");
          if (fills[layer.id]) map.setPaintProperty(layer.id,"fill-color",fills[layer.id]);
          if (roads[layer.id]) map.setPaintProperty(layer.id,"line-color",roads[layer.id]);
          if (layer.type === "symbol" && layer.layout?.["text-field"]) {
            map.setPaintProperty(layer.id,"text-color",layer.id.startsWith("place_city") ? "#e6e8ec" : "#c5cbd4");
            map.setPaintProperty(layer.id,"text-halo-color","#24262a");
            map.setPaintProperty(layer.id,"text-halo-width",1.3);
          }
        });
      }
      baseLayers = map.getStyle().layers.map(layer => ({id:layer.id, visibility:layer.layout?.visibility || "visible"}));
      map.addSource("satellite-imagery", {type:"raster", tiles:["https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2024_3857/default/g/{z}/{y}/{x}.jpg"], tileSize:256, maxzoom:14, attribution:'<a href="https://cloudless.eox.at/">EOxCloudless</a> by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2024) · <a href="https://cloudless.eox.at/license-non-commercial">CC BY-NC-SA 4.0</a>'});
      map.addLayer({id:"satellite-imagery", type:"raster", source:"satellite-imagery", layout:{visibility:"none"}, paint:{"raster-fade-duration":0}});
      map.addSource("selected-route", {type:"geojson", data:{type:"FeatureCollection", features:[]}, attribution:"Route corridors: City of Edmonton / ETS · simulated movement"});
      map.addLayer({id:"selected-route-casing", type:"line", source:"selected-route", paint:{"line-color":dark ? "#19191b" : "#ffffff", "line-width":7, "line-opacity":0.9}});
      map.addLayer({id:"selected-route-line", type:"line", source:"selected-route", paint:{"line-color":dark ? "#a6bcd8" : "#285d91", "line-width":3}});
      applyMode(); route(); note.hidden = true;
      map.once("idle", () => { focusSelected(selected); collapseCredits(); });
    });
    map.on("error", event => {
      // A single tile or the hidden satellite layer failing is not a basemap outage.
      const satellite = event?.sourceId === "satellite-imagery";
      if (satellite ? mode !== "satellite" : event?.sourceId || event?.tile || map.isStyleLoaded()) return;
      note.hidden = false; retry.hidden = false;
      message.textContent = satellite ? "Satellite imagery could not load. Try Streets or retry the map." : "Basemap unavailable. Vehicle positions remain available in the list.";
    });
    document.addEventListener("voltwatch:theme", () => { busy(); map.setStyle(style(), {diff:false}); });
    document.querySelectorAll("[data-map-mode]").forEach(button => button.addEventListener("click", () => {mode = button.dataset.mapMode; if (map?.getLayer("satellite-imagery")) note.hidden = true; applyMode();}));
  } catch {
    note.hidden = false; retry.hidden = false; message.textContent = "Map unavailable. Check WebGL support and your connection; vehicle positions remain in the list.";
    document.querySelectorAll("[data-map-mode]").forEach(button => button.disabled = true);
  }
  retry.addEventListener("click", () => {
    if (!map) { location.reload(); return; }
    note.hidden = false; retry.hidden = true; message.textContent = "Loading map tiles…";
    map.setStyle(style(), {diff:false});
  });
  function update(buses, id) {
    fleet = buses; selected = id;
    if (!map) return;
    const ids = new Set();
    fleet.forEach(bus => {
      const p = bus.position;
      if (!p || !Number.isFinite(Number(p.lat)) || !Number.isFinite(Number(p.lng))) return;
      ids.add(bus.bus_id);
      let item = markers.get(bus.bus_id);
      if (!item) {
        // The map positions the wrapper with a CSS transform; styling lives on the inner button
        // so hover or button styles can never move the bus off its route.
        const pin = document.createElement("div");
        pin.className = "bus-pin";
        const element = document.createElement("button");
        element.type = "button";
        element.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M8 6v6"/><path d="M15 6v6"/><path d="M2 12h19.6"/><path d="M18 18h3s.5-1.7.8-2.8c.1-.4.2-.8.2-1.2 0-.4-.1-.8-.2-1.2l-1.4-5C20.1 6.8 19.1 6 18 6H4a2 2 0 0 0-2 2v10h3"/><circle cx="7" cy="18" r="2"/><path d="M9 18h5"/><circle cx="16" cy="18" r="2"/></svg><span class="bus-number"></span>`;
        element.addEventListener("click", () => select(bus.bus_id));
        pin.append(element);
        item = {element, marker:new maplibregl.Marker({element:pin, anchor:"center"}).setLngLat([p.lng,p.lat]).addTo(map)};
        markers.set(bus.bus_id,item);
      }
      const stale = bus.position_stale || ["NO_SIGNAL","OFFLINE"].includes(bus.link_state);
      item.element.className = `bus-marker ${stale ? "unknown" : severity(bus.severity)} ${selected === bus.bus_id ? "selected" : ""}`;
      item.element.querySelector(".bus-number").textContent = bus.route ? `Bus ${bus.route.number}` : bus.bus_id;
      item.element.setAttribute("aria-label", `${bus.route ? `Bus ${bus.route.number}, route ${bus.route.name}` : `Bus ${bus.bus_id}`}, ${stale ? "position stale or no signal" : bus.severity || "unknown"}, simulated position`);
      item.element.setAttribute("aria-pressed", String(selected === bus.bus_id));
      item.marker.setLngLat([p.lng,p.lat]);
    });
    for (const [id,item] of markers) if (!ids.has(id)) {item.marker.remove(); markers.delete(id);}
    if (!fitted && ids.size) {
      const bounds = new maplibregl.LngLatBounds();
      for (const id of ids) bounds.extend(markers.get(id).marker.getLngLat());
      map.fitBounds(bounds, {padding:60, maxZoom:14, animate:false}); fitted = true;
    }
    route(); updateFocus();
  }
  function focusSelected(id) {
    const item = markers.get(id);
    if (!map || !item || matchMedia("(max-width: 700px)").matches) return;
    const point = map.project(item.marker.getLngLat());
    const right = document.getElementById("drawer").getBoundingClientRect().left - document.getElementById("map").getBoundingClientRect().left - 48;
    const dx = point.x > right ? point.x - right : point.x < 48 ? point.x - 48 : 0;
    const bottom = map.getContainer().clientHeight - 80;
    const dy = point.y > bottom ? point.y - bottom : point.y < 60 ? point.y - 60 : 0;
    if (dx || dy) map.panBy([dx, dy], {animate:!matchMedia("(prefers-reduced-motion: reduce)").matches});
  }
  return {update, focusSelected};
}
