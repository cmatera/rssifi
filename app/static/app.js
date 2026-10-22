// Relative timestamps
const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
function rel(date) {
  const s = (date - Date.now()) / 1000, abs = Math.abs(s);
  const units = [["year", 31536000], ["month", 2592000], ["day", 86400], ["hour", 3600], ["minute", 60]];
  for (const [u, n] of units) if (abs >= n) return rtf.format(Math.round(s / n), u);
  return rtf.format(Math.round(s), "second");
}
document.querySelectorAll("[data-time]").forEach((el) => {
  let iso = el.dataset.time;
  if (!/[zZ]|[+-]\d\d:?\d\d$/.test(iso)) iso += "Z"; // stored as UTC
  const d = new Date(iso);
  el.textContent = rel(d);
  el.title = d.toLocaleString();
});

// Copy buttons
document.querySelectorAll("[data-copy]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(btn.dataset.copy); } catch { prompt("Copy feed URL:", btn.dataset.copy); return; }
    const old = btn.textContent; btn.textContent = "Copied!"; setTimeout(() => (btn.textContent = old), 1500);
  });
});

// Confirm destructive forms
document.querySelectorAll("form[data-confirm]").forEach((f) =>
  f.addEventListener("submit", (e) => { if (!confirm(f.dataset.confirm)) e.preventDefault(); })
);

// Site form behaviour
const form = document.getElementById("site-form");
if (form) {
  const panels = form.querySelectorAll(".mode-panel");
  const syncMode = () => {
    const mode = form.querySelector("input[name=mode]:checked")?.value;
    panels.forEach((p) => p.classList.toggle("show", p.dataset.mode === mode));
  };
  form.querySelectorAll("input[name=mode]").forEach((r) => r.addEventListener("change", syncMode));
  syncMode();

  const preset = document.getElementById("interval-preset");
  const minutes = form.querySelector("input[name=interval_minutes]");
  const syncPreset = () => {
    const match = [...preset.options].find((o) => o.value === minutes.value);
    preset.value = match ? match.value : "custom";
  };
  preset.addEventListener("change", () => {
    if (preset.value !== "custom") minutes.value = preset.value; else minutes.focus();
    form.querySelector("input[name=schedule_type][value=interval]").checked = true;
  });
  minutes.addEventListener("input", syncPreset);
  form.querySelector("input[name=cron]").addEventListener("focus", () => {
    form.querySelector("input[name=schedule_type][value=cron]").checked = true;
  });
  syncPreset();

  const out = document.getElementById("preview");
  document.getElementById("preview-btn").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true; btn.textContent = "Loading…"; out.innerHTML = "";
    try {
      const res = await fetch("/preview", { method: "POST", body: new FormData(form) });
      out.innerHTML = await res.text();
    } catch (err) {
      out.innerHTML = `<p class="error">Preview failed: ${err}</p>`;
    } finally {
      btn.disabled = false; btn.textContent = "Preview items";
    }
  });
}
