"use strict";

(function() {
  // HTML-escape any user-supplied string
  function sanitizeHTML(str) {
    const tmp = document.createElement("div");
    tmp.textContent = str;
    return tmp.innerHTML;
  }

  // Render the returned JSON safely
  function renderInsights(data) {
    const resultDiv = document.getElementById("result");
    if (!resultDiv) return;
    if (data.detail) {
      const msg = sanitizeHTML(data.detail);
      resultDiv.innerHTML = `<div style="color:red;"><strong>Error:</strong> ${msg}</div>`;
      return;
    }
    if (data.llm_report) {
      const escaped = sanitizeHTML(data.llm_report);
      resultDiv.innerHTML = `<div class="result">${escaped.replace(/\n/g, "<br>")}</div>`;
      return;
    }
    resultDiv.textContent = "No insights available.";
  }

  // Runaway CTA handler
  function runawayButtonCheck(buttonId, inputs) {
    const button = document.getElementById(buttonId);
    if (!button) return;
    let hoverCount = 0;
    button.addEventListener("mouseenter", e => {
      const allFilled = inputs.every(id => {
        const el = document.getElementById(id);
        return el && el.value && el.value.trim().length > 0;
      });
      if (!allFilled) {
        const offset = 100 + 30 * (hoverCount++ % 5);
        const dir = hoverCount % 2 === 0 ? 1 : -1;
        button.style.transform = `translateX(${offset * dir}px)`;
        button.style.transition = "transform 0.3s ease";

        const tooltip = document.getElementById("tooltip");
        if (!tooltip) return;
        tooltip.style.display = "block";
        tooltip.style.top = `${e.clientY + 10}px`;
        tooltip.style.left = `${e.clientX + 10}px`;
        tooltip.textContent = "Please fill in the required fields first.";

        setTimeout(() => {
          button.style.transform = "translateX(0)";
          tooltip.style.display = "none";
        }, 1000);
      }
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    // CSV form submission
    const csvForm = document.getElementById("csv-form");
    if (csvForm) {
      csvForm.addEventListener("submit", async e => {
        e.preventDefault();
        const resultDiv = document.getElementById("result");
        if (resultDiv) resultDiv.textContent = "Analyzing...";
        try {
          const resp = await fetch("/insights/csv", {
            method: "POST",
            body: new FormData(csvForm)
          });
          const json = await resp.json();
          renderInsights(json);
        } catch (err) {
          renderInsights({ detail: "Network error." });
        }
      });
    }

    // Google form submission
    const googleForm = document.getElementById("google-form");
    if (googleForm) {
      googleForm.addEventListener("submit", async e => {
        e.preventDefault();
        const resultDiv = document.getElementById("result");
        if (resultDiv) resultDiv.textContent = "Analyzing...";
        try {
          const resp = await fetch("/insights/google", {
            method: "POST",
            body: new FormData(googleForm)
          });
          const json = await resp.json();
          renderInsights(json);
        } catch (err) {
          renderInsights({ detail: "Network error." });
        }
      });
    }

    // Bind runaway CTAs
    runawayButtonCheck("csv-submit",    ["csvFile"]);
    runawayButtonCheck("google-submit", ["name", "city"]);
  });
})();