(() => {
  "use strict";

  const body = document.body;
  const SEARCH_TIMEOUT_MS = Number(body.dataset.searchTimeoutMs);
  const BUILD_TIMEOUT_MS = Number(body.dataset.buildTimeoutMs);
  const LM_BASE = body.dataset.lmBase;

  const form = document.getElementById("search-form");
  const q = document.getElementById("q");
  const go = document.getElementById("go");
  const statusBox = document.getElementById("status");
  const intentBox = document.getElementById("intent");
  const rigBox = document.getElementById("rig");
  const resultsWrap = document.getElementById("results-wrap");
  const fullRigsOnly = document.getElementById("full-rigs-only");
  const fullRigsFilter = document.getElementById("full-rigs-filter");
  const webResearch = document.getElementById("web-research");

  const builderTab = document.getElementById("builder-tab");
  const searchTab = document.getElementById("search-tab");
  const builderPanel = document.getElementById("builder-panel");
  const searchPanel = document.getElementById("search-panel");
  const tabs = [builderTab, searchTab];

  let lastToneSearch = null;
  let searchGeneration = 0;
  let workspace = "builder";

  function esc(s) {
    return String(s ?? "").replace(
      /[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c])
    );
  }

  // ---------------------------------------------------------------------
  // Tabs (WAI-ARIA tabs pattern: roving tabindex + arrow-key navigation)
  // ---------------------------------------------------------------------

  function activateTab(tab, { focus = false } = {}) {
    const next = tab === searchTab ? "search" : "builder";
    setWorkspace(next);
    if (focus) tab.focus();
  }

  document.querySelector(".tabs").addEventListener("click", (e) => {
    const tab = e.target.closest(".tab");
    if (tab) activateTab(tab);
  });

  document.querySelector(".tabs").addEventListener("keydown", (e) => {
    const currentIndex = tabs.indexOf(document.activeElement);
    if (currentIndex === -1) return;
    let nextIndex = null;
    if (e.key === "ArrowRight") nextIndex = (currentIndex + 1) % tabs.length;
    else if (e.key === "ArrowLeft") nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") nextIndex = 0;
    else if (e.key === "End") nextIndex = tabs.length - 1;
    if (nextIndex === null) return;
    e.preventDefault();
    activateTab(tabs[nextIndex], { focus: true });
  });

  function setWorkspace(next) {
    workspace = next;
    const builder = next === "builder";

    for (const tab of tabs) {
      const selected = tab === (builder ? builderTab : searchTab);
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
    }

    // Each tabpanel stays visible whenever its own tab is selected — the "no
    // search yet" case is communicated by the shared status panel below, not
    // by hiding the panel a screen-reader user just navigated into.
    builderPanel.hidden = !builder;
    searchPanel.hidden = builder;
    fullRigsFilter.hidden = builder;
    go.textContent = builder ? "Build my tone" : "Find NAM amp/cab captures";

    const hasSearch = Boolean(lastToneSearch);
    intentBox.hidden = !hasSearch;
    rigBox.hidden = !rigBox.innerHTML;

    if (!hasSearch) {
      statusBox.hidden = false;
      statusBox.innerHTML = builder
        ? "<p>Describe a tone to find the best built-in GP-50 amp, cab, and supporting effects.</p>"
        : "<p>Describe the amp/cab character you need. We will search and AI-rank full-rig NAM captures; this does not build a GP-50 preset.</p>";
    }
  }

  // ---------------------------------------------------------------------
  // Example chips
  // ---------------------------------------------------------------------

  document.querySelector(".examples-list").addEventListener("click", (e) => {
    const chip = e.target.closest(".example-chip");
    if (!chip) return;
    q.value = chip.textContent;
    search();
  });

  // ---------------------------------------------------------------------
  // Search
  // ---------------------------------------------------------------------

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    search();
  });

  async function search() {
    const query = q.value.trim();
    if (!query) {
      q.focus();
      return;
    }

    const existingIntent = workspace === "search" ? lastToneSearch?.intent : null;
    const generation = ++searchGeneration;
    go.disabled = true;
    resultsWrap.innerHTML = "";
    if (workspace === "builder") {
      rigBox.innerHTML = "";
      rigBox.hidden = true;
      lastToneSearch = null;
      intentBox.hidden = true;
    }
    statusBox.hidden = false;
    statusBox.innerHTML = `<p>${
      existingIntent
        ? "Finding full-rig NAM captures, then AI-ranking them against your amp requirements&hellip;"
        : webResearch.checked
        ? "Researching and interpreting the tone&hellip;"
        : "Interpreting the tone&hellip;"
    }</p>`;

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), SEARCH_TIMEOUT_MS);
    try {
      const r = await fetch("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          query,
          full_rigs_only: fullRigsOnly.checked,
          // NAM browsing is a separate optional follow-up after the tone
          // builder already ran (existingIntent set): do not repeat the
          // potentially slow web-tool call there. A direct search-tab query
          // has no prior intent to reuse, so it honors the checkbox just
          // like the tone builder does.
          web_research: webResearch.checked,
          find_nam: workspace === "search",
          intent: existingIntent,
        }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Search failed");
      if (generation !== searchGeneration) return;

      statusBox.hidden = true;
      lastToneSearch = {
        query,
        intent: data.intent || {},
        research_notes: (data.intent || {}).research_notes || "",
        results: data.results || [],
      };

      renderIntent(data.intent || {}, query);
      renderResults(data.results || []);
    } catch (e) {
      statusBox.hidden = false;
      const message =
        e.name === "AbortError"
          ? 'The search took longer than 3 minutes. Check that a local LLM server has a model loaded, then try again with "Research this tone on the web" turned off.'
          : e.message;
      statusBox.innerHTML = `<p class="error-text">${esc(message)}</p>`;
    } finally {
      clearTimeout(timeout);
      go.disabled = false;
    }
  }

  function renderIntent(i, query) {
    const chips = [
      ...(i.artist ? [i.artist] : []),
      ...(i.song ? [i.song] : []),
      ...(i.character || []),
      ...(i.styles || []),
    ].filter(Boolean);

    const effects = (i.effects || [])
      .map(
        (effect) => `
      <div class="effect"><strong>${esc(effect.name)}</strong> &mdash; ${esc(effect.purpose)}
      <span class="effect-meta">(${esc(effect.starting_point)})</span></div>
    `
      )
      .join("");

    const MODE_LABELS = {
      song_reconstruction: "Song reconstruction",
      artist_general: "Artist's general sound",
      descriptive_tone: "Descriptive tone",
      hybrid: "Reference + custom changes",
    };
    const rejectedEffects = (i.rejected_effects || [])
      .map(
        (effect) => `
      <div class="effect"><strong>${esc(effect.name)}</strong> &mdash; ${esc(effect.reason)}</div>
    `
      )
      .join("");

    intentBox.innerHTML = `
      <h2>AI interpretation</h2>
      ${i.mode ? `<p class="meta">Reading this as: ${esc(MODE_LABELS[i.mode] || i.mode)}</p>` : ""}
      <p>${esc(i.summary || query)}</p>
      <ul class="chips">${chips.map((x) => `<li class="chip">${esc(x)}</li>`).join("")}</ul>
      <div class="rig-guide">
        <p><strong>Suggested amp:</strong> ${esc(
          (i.amp_families || []).join(" or ") || "GP-50's own catalogue amp, chosen to match this tone"
        )}${i.gain ? ` &middot; gain: ${esc(i.gain)}` : ""}</p>
        <p><strong>Suggested guitar:</strong> ${esc(
          i.guitar || "Use the guitar you have; adjust pickup selection and gain to taste."
        )}</p>
        ${effects ? `<div class="effect"><strong>Helpful effects</strong></div>${effects}` : ""}
      </div>
      <div class="rig-guide">
        <p><strong>Recommended starting point: GP-50 built-in amp and cab</strong></p>
        <p>We&rsquo;ll choose the standard GP-50 amp and cabinet that best match this tone, then add only the supporting effects that help.</p>
        <button type="button" id="build-rig-btn">Build preset</button>
      </div>
      <div class="rig-guide">
        <p><strong>Want to audition alternatives?</strong></p>
        <p>NAM captures are optional. Browse suggestions separately without changing the GP-50 preset path.</p>
        <button type="button" class="secondary" id="browse-nam-btn">Browse optional NAM amp/cab captures</button>
      </div>
      ${i.interpretation_warning ? `<p class="error-text">${esc(i.interpretation_warning)}</p>` : ""}
      ${i.research_warning ? `<p class="meta">${esc(i.research_warning)}</p>` : ""}
      ${
        rejectedEffects
          ? `<details class="research-notes"><summary>Effects considered but excluded (insufficient evidence)</summary>${rejectedEffects}</details>`
          : ""
      }
      ${
        i.research_notes
          ? `<details class="research-notes"><summary>${
              i.research_used ? "Web research notes (used for this result)" : "Web research notes"
            }</summary><textarea readonly aria-label="Raw web research notes">${esc(
              i.research_notes
            )}</textarea></details>`
          : ""
      }
    `;
    intentBox.hidden = false;

    document.getElementById("build-rig-btn")?.addEventListener("click", (e) => buildBuiltinRig(e.target));
    document.getElementById("browse-nam-btn")?.addEventListener("click", () => activateTab(searchTab));
  }

  function renderResults(results) {
    if (workspace === "search" && !results.length) {
      resultsWrap.innerHTML =
        '<div class="panel"><p><strong>Optional NAM captures</strong><br>No TONE3000 captures were found. You can still build the GP-50 built-in amp and cab preset above.</p></div>';
      return;
    }

    const items = results
      .map((x, idx) => {
        const t = x.tone;
        const makes = (t.makes || []).map((v) => (typeof v === "string" ? v : v.name)).filter(Boolean);
        const tags = (t.tags || []).map((v) => (typeof v === "string" ? v : v.name)).filter(Boolean);
        const creator = t.user?.display_name || t.user?.username || "";
        return `
        <li class="card">
          <div class="score">${esc(x.score)}%</div>
          <div>
            <div class="titleline">
              <h3>${idx + 1}. ${esc(t.title)}</h3>
              ${
                t.a2_models_count
                  ? `<span class="chip">${esc(t.a2_models_count)} A2 model${
                      t.a2_models_count == 1 ? "" : "s"
                    }</span>`
                  : ""
              }
            </div>
            <p class="meta">
              ${creator ? `by ${esc(creator)} &middot; ` : ""}
              ${makes.length ? esc(makes.join(", ")) + " &middot; " : ""}
              ${tags.slice(0, 5).map(esc).join(" &middot; ")}
            </p>
            <p class="reason">${esc(x.reason)}</p>
            <div class="links">
              ${
                t.url
                  ? `<a class="btn" href="${esc(t.url)}" target="_blank" rel="noopener">Open on TONE3000</a>`
                  : ""
              }
              <button type="button" class="secondary show-models-btn" data-tone-id="${Number(t.id)}">
                View downloadable NAM captures
              </button>
            </div>
            <ul class="models" id="models-${Number(t.id)}" hidden></ul>
          </div>
        </li>`;
      })
      .join("");

    resultsWrap.innerHTML = `
      <h2 class="results-heading">Optional TONE3000 NAM captures</h2>
      <p class="results-intro">These are alternatives to audition. Download one, import it into a GP-50 SnapTone slot in Valeton Suite, then build a preset around that capture if you prefer it.</p>
      <ul class="results">${items}</ul>
    `;

    for (const btn of resultsWrap.querySelectorAll(".show-models-btn")) {
      btn.addEventListener("click", () => showModels(Number(btn.dataset.toneId), btn));
    }
  }

  async function showModels(toneId, button) {
    const box = document.getElementById(`models-${toneId}`);
    if (box.dataset.loaded === "1") {
      box.hidden = !box.hidden;
      return;
    }

    button.disabled = true;
    button.textContent = "Loading…";
    try {
      // Rank the (often 40+) captures under one tone by the same character/
      // gain/style words the tone search already produced, so the list isn't
      // just whatever order the API happened to return.
      const intent = lastToneSearch?.intent || {};
      const terms = [...(intent.character || []), ...(intent.gain ? [intent.gain] : []), ...(intent.styles || [])].filter(
        Boolean
      );
      const url = terms.length
        ? `/api/models/${toneId}?terms=${encodeURIComponent(terms.join(","))}`
        : `/api/models/${toneId}`;
      const r = await fetch(url);
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Could not load models");

      const models = data.data || [];
      box.innerHTML =
        models
          .map(
            (m, idx) => `
        <li class="model">
          <span>${esc(m.name)} <span class="chip">${esc(m.size || "A2")}</span>${
              idx === 0 && m.match_score > 0 ? ' <span class="chip">Closest match</span>' : ""
            }</span>
          <span class="links"><a class="btn" href="/api/download-model/${encodeURIComponent(
            m.id
          )}">Download NAM capture</a></span>
        </li>
      `
          )
          .join("") || '<li class="model">No downloadable NAM captures were returned.</li>';
      box.dataset.loaded = "1";
      box.hidden = false;
    } catch (e) {
      box.innerHTML = `<li class="model error-text">${esc(e.message)}</li>`;
      box.hidden = false;
    } finally {
      button.disabled = false;
      button.textContent = "View downloadable NAM captures";
    }
  }

  async function buildBuiltinRig(button) {
    const state = lastToneSearch;
    if (!state) {
      window.alert("Please run a tone search first.");
      return;
    }
    return requestRig(
      { query: state.query, intent: state.intent, research_notes: state.research_notes },
      button,
      "Build preset"
    );
  }

  async function requestRig(payload, button, idleText) {
    button.disabled = true;
    button.textContent = "Building…";
    statusBox.hidden = false;
    statusBox.innerHTML =
      "<p>Choosing the GP-50 amp, cab and supporting effects with your local LLM&hellip; This normally takes under a minute. Keep it running with a model loaded.</p>";
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), BUILD_TIMEOUT_MS);
    try {
      const r = await fetch("/api/build-rig", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify(payload),
      });
      const data = await r.json();
      if (!r.ok) {
        const validation = (data.validation_errors || []).join("\n");
        throw new Error([data.error, validation].filter(Boolean).join("\n\n") || "Rig build failed");
      }
      showRig(data.rig);
      statusBox.hidden = true;
    } catch (e) {
      const detail =
        e.name === "AbortError"
          ? `Rig building timed out. Check that a local LLM server is running at ${esc(
              LM_BASE
            )} with a model loaded, then try again. A large/reasoning model can be slow — raise LM_JSON_TIMEOUT if this happens often.`
          : e.message;
      statusBox.hidden = false;
      statusBox.innerHTML = `<p class="error-text">${esc(detail)}</p>`;
    } finally {
      clearTimeout(timeout);
      button.disabled = false;
      button.textContent = idleText;
    }
  }

  function showRig(rig) {
    const blocks = (rig.signal_chain || [])
      .map(
        (b) => `<div class="effect"><strong>${esc(b.module)} &middot; ${esc(b.effect_name)}</strong>${
          b.origin ? ` <span class="effect-meta">(${esc(b.origin)})</span>` : ""
        }<br>${esc(b.purpose || "")}<br>${Object.entries(b.parameters || {})
          .map(([k, v]) => `${esc(k)} ${esc(v)}`)
          .join(" &middot; ")}</div>`
      )
      .join("");
    const review = (rig.effect_review || []).map((note) => `<div class="effect">${esc(note)}</div>`).join("");
    rigBox.innerHTML = `
      <h2>Your GP-50 preset &mdash; ${esc(rig.preset_name)}</h2>
      <p>${esc(rig.summary || "")}</p>
      ${rig.validation_warning ? `<p class="error-text">${esc(rig.validation_warning)}</p>` : ""}
      ${blocks}
      ${review ? `<div class="rig-guide"><strong>Effect review</strong>${review}</div>` : ""}
      <div class="links"><button type="button" id="download-preset-btn">Download GP-50 preset</button></div>
      <p class="meta">${esc(rig.snaptone_status || "")}</p>
      <details><summary>Advanced: edit preset data</summary><textarea id="rig-json" aria-label="Editable GP-50 preset data">${esc(
        JSON.stringify(rig, null, 2)
      )}</textarea></details>
    `;
    rigBox.hidden = false;
    rigBox.scrollIntoView({ behavior: "smooth", block: "start" });
    document.getElementById("download-preset-btn").addEventListener("click", downloadPreset);
  }

  async function downloadPreset() {
    let rig;
    try {
      rig = JSON.parse(document.getElementById("rig-json").value);
    } catch (e) {
      window.alert("Rig JSON is not valid: " + e.message);
      return;
    }
    try {
      const r = await fetch("/api/create-preset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(rig),
      });
      if (!r.ok) {
        const e = await r.json();
        throw new Error(e.error || "Preset creation failed");
      }
      const blob = await r.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      // Use the server's own filename (app.py's preset_filename()) instead
      // of re-deriving one here: this used to duplicate that sanitization
      // logic client-side, so the two could silently drift apart.
      const disposition = r.headers.get("Content-Disposition") || "";
      const match = /filename="?([^";]+)"?/i.exec(disposition);
      a.download = match ? match[1] : "gp50preset.prst";
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      window.alert(e.message);
    }
  }
})();
