// Runs Python (Pyodide) off the main thread so the page stays responsive during a simulation.
importScripts("https://cdn.jsdelivr.net/pyodide/v0.27.2/full/pyodide.js");

const say = (text) => postMessage({ type: "status", text });
let request;

const ready = (async () => {
  say("Loading Python (about 10 MB, first visit only)...");
  const py = await loadPyodide();
  say("Loading libraries...");
  await py.loadPackage("pyyaml");
  say("Loading bridge-island...");
  const zip = await (await fetch("bridge_island_web.zip")).arrayBuffer();
  py.unpackArchive(zip, "zip", { extractDir: "/web" });
  py.runPython(
    'import os, sys\nsys.path.insert(0, "/web")\nos.environ["BRIDGE_ISLAND_EXAMPLES"] = "/web/examples"'
  );
  request = py.pyimport("bridge_web").request;
  postMessage({ type: "ready" });
})().catch((err) => postMessage({ type: "fatal", text: String(err) }));

onmessage = async (event) => {
  const { id, method, target, body } = event.data;
  await ready;
  if (!request) return;
  try {
    const proxy = request(method, target, body);
    const [status, type, disposition, data] = proxy.toJs();
    proxy.destroy();
    postMessage({ type: "response", id, status, contentType: type, disposition, data });
  } catch (err) {
    postMessage({ type: "error", id, text: String(err) });
  }
};
