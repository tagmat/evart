(() => {
  const fieldNames = ["consumes", "publishes", "database_payloads"];
  const storagePrefix = "evart:service-list";

  const normalize = (value) =>
    value.replace(/\s+/g, " ").trim().toLowerCase();

  const getOptions = (select) => Array.from(select?.options || []);

  const readChosenText = (toSelect) =>
    getOptions(toSelect)
      .map((option) => option.text.trim())
      .filter(Boolean);

  const applyList = (lines, fromSelect, toSelect) => {
    const fromOptions = getOptions(fromSelect);
    const toOptions = getOptions(toSelect);
    const fromByValue = new Map();
    const fromByText = new Map();

    fromOptions.forEach((option) => {
      fromByValue.set(option.value, option);
      const key = normalize(option.text);
      if (!fromByText.has(key)) {
        fromByText.set(key, option);
      }
    });

    const toByValue = new Set(toOptions.map((option) => option.value));
    const toByText = new Set(
      toOptions.map((option) => normalize(option.text))
    );

    let added = 0;
    let already = 0;
    let missing = 0;

    fromOptions.forEach((option) => {
      option.selected = false;
    });

    lines.forEach((line) => {
      const key = normalize(line);
      if (!key) {
        return;
      }

      if (toByValue.has(line) || toByText.has(key)) {
        already += 1;
        return;
      }

      const match = fromByValue.get(line) || fromByText.get(key);
      if (match) {
        match.selected = true;
        added += 1;
      } else {
        missing += 1;
      }
    });

    if (added > 0) {
      if (window.SelectBox && typeof window.SelectBox.move === "function") {
        window.SelectBox.move(fromSelect.id, toSelect.id);
      } else {
        const selectedOptions = fromOptions.filter((option) => option.selected);
        selectedOptions.forEach((option) => {
          const clone = option.cloneNode(true);
          clone.selected = true;
          toSelect.appendChild(clone);
          option.remove();
        });
      }
    }

    return { added, already, missing };
  };

  const ensureWidget = (fieldName) => {
    const toSelect = document.getElementById(`id_${fieldName}_to`);
    const fromSelect = document.getElementById(`id_${fieldName}_from`);
    if (!toSelect || !fromSelect) {
      return false;
    }

    const selector =
      toSelect.closest(".selector") || fromSelect.closest(".selector");
    if (!selector) {
      return false;
    }

    if (
      selector.querySelector(
        `.selector-copy-paste[data-field="${fieldName}"]`
      )
    ) {
      return true;
    }

    const chosenBox = selector.querySelector(".selector-chosen") || selector;
    const container = document.createElement("div");
    container.className = "selector-copy-paste";
    container.dataset.field = fieldName;
    container.style.marginTop = "0.75em";

    const copyButton = document.createElement("button");
    copyButton.type = "button";
    copyButton.className = "button";
    copyButton.textContent = "Copy chosen";
    copyButton.style.marginRight = "0.5em";

    const pasteButton = document.createElement("button");
    pasteButton.type = "button";
    pasteButton.className = "button";
    pasteButton.textContent = "Paste chosen";

    const status = document.createElement("div");
    status.className = "selector-copy-paste-status";
    status.style.marginTop = "0.35em";
    status.style.fontSize = "12px";
    status.style.color = "#666";

    container.append(copyButton, pasteButton, status);
    chosenBox.appendChild(container);

    const storageKey = `${storagePrefix}:${fieldName}`;

    const setStatus = (message) => {
      status.textContent = message;
    };

    copyButton.addEventListener("click", async () => {
      const lines = readChosenText(toSelect);
      if (!lines.length) {
        setStatus("No items to copy.");
        return;
      }

      const text = lines.join("\n");
      localStorage.setItem(storageKey, text);

      let clipboardOk = false;
      if (navigator.clipboard && window.isSecureContext) {
        try {
          await navigator.clipboard.writeText(text);
          clipboardOk = true;
        } catch (error) {
          clipboardOk = false;
        }
      } else {
        try {
          const temp = document.createElement("textarea");
          temp.value = text;
          temp.setAttribute("readonly", "");
          temp.style.position = "fixed";
          temp.style.opacity = "0";
          document.body.appendChild(temp);
          temp.select();
          clipboardOk = document.execCommand("copy");
          document.body.removeChild(temp);
        } catch (error) {
          clipboardOk = false;
        }
      }

      const suffix = clipboardOk ? " to clipboard." : ".";
      setStatus(`Copied ${lines.length} item${lines.length === 1 ? "" : "s"}${suffix}`);
    });

    pasteButton.addEventListener("click", async () => {
      let text = "";

      if (navigator.clipboard && window.isSecureContext) {
        try {
          text = await navigator.clipboard.readText();
        } catch (error) {
          text = "";
        }
      }

      if (!text) {
        text = localStorage.getItem(storageKey) || "";
      }

      if (!text) {
        text = window.prompt("Paste list (one per line):", "") || "";
      }

      if (!text) {
        setStatus("Nothing to paste.");
        return;
      }

      localStorage.setItem(storageKey, text);
      const lines = text
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean);

      if (!lines.length) {
        setStatus("Nothing to paste.");
        return;
      }

      const result = applyList(lines, fromSelect, toSelect);
      setStatus(
        `Added ${result.added}, already selected ${result.already}, missing ${result.missing}.`
      );
    });

    return true;
  };

  const boot = () => {
    let initialized = 0;
    fieldNames.forEach((fieldName) => {
      if (ensureWidget(fieldName)) {
        initialized += 1;
      }
    });
    return initialized > 0;
  };

  const start = () => {
    if (boot()) {
      return;
    }

    let attempts = 0;
    const timer = setInterval(() => {
      attempts += 1;
      if (boot() || attempts > 10) {
        clearInterval(timer);
      }
    }, 150);
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
