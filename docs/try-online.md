# Try it in your browser

You can run the app without installing anything: **[open the in-browser app](online/index.html)**.

It is the same app as `bridge-island ui`, with the same pages (run a simulation, scenario files,
full report, guideline checks, experiments, help). Python runs inside your browser using
[Pyodide](https://pyodide.org). Nothing you enter is uploaded, and nothing is saved unless you
download it.

What to expect:

* The first visit downloads about 10 MB (Python for the browser); the browser keeps it, so later
  visits start faster.
* A long simulation takes longer than on your own computer, because Python in a browser is slower.
  The page stays responsive while it runs.
* The experiment plots are not drawn in the browser (they need matplotlib); the tables and the
  CSV files are. For the plots, run the app on your own computer: see [Using the app](app.md).
* It needs a current browser with JavaScript and WebAssembly (any recent Chrome, Edge, Firefox or Safari).

All content is invented and all numbers are synthetic, illustrative.
