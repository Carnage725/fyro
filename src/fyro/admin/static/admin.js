/* Progressive enhancement for the fyro admin. CSP-safe: external file, no inline handlers. */
(() => {
  const main = () => document.getElementById("main");
  const modal = () => document.getElementById("modal");
  const modalContent = () => document.getElementById("modal-content");
  let lastTrigger = null;
  let restoreSearchFocus = false;

  const rowBoxes = () =>
    Array.from(document.querySelectorAll('#results tbody input[type="checkbox"][name="ids"]'));

  function closeFilterMenus(except) {
    for (const menu of document.querySelectorAll(".filter-menu[open]")) {
      if (menu !== except) menu.open = false;
    }
  }

  function syncBodyState() {
    document.body.classList.toggle("list-view", Boolean(document.querySelector("#results")));
  }

  function updateSelection() {
    const boxes = rowBoxes();
    const checked = boxes.filter((box) => box.checked);
    const selectAll = document.querySelector("[data-select-all]");

    if (selectAll) {
      selectAll.checked = boxes.length > 0 && checked.length === boxes.length;
      selectAll.indeterminate = checked.length > 0 && checked.length < boxes.length;
      selectAll.disabled = boxes.length === 0;
    }

    const count = document.querySelector("[data-selection-count]");
    if (count) {
      count.textContent = checked.length
        ? `${checked.length} selected`
        : boxes.length
          ? "Select rows"
          : "0 selected";
    }

    const bulkDelete = document.querySelector("[data-bulk-delete]");
    if (bulkDelete) bulkDelete.disabled = checked.length === 0;
  }

  function announceResults() {
    const status = document.getElementById("result-status");
    const count = document.querySelector("#results .result-count");
    if (status && count) status.textContent = count.textContent.trim();
  }

  function openDialog() {
    const element = modal();
    if (!element) return;
    if (!element.open) element.showModal();
    document.documentElement.classList.add("modal-open");
  }

  function closeDialog() {
    const element = modal();
    if (element?.open) element.close();
  }

  function showLoading() {
    const content = modalContent();
    if (!content) return;
    content.innerHTML =
      '<div class="modal-loading" role="status">' +
      '<h2 id="modal-heading" class="sr-only">Loading</h2>' +
      "<span>Loading…</span>" +
      "</div>";
    openDialog();
    modal()?.querySelector(".modal-close")?.focus({ preventScroll: true });
  }

  function focusFirstControl() {
    const content = modalContent();
    if (!content) return;
    const invalid = content.querySelector(
      ".field.invalid input, .field.invalid textarea, .field.invalid select"
    );
    if (invalid) {
      invalid.focus({ preventScroll: true });
      invalid.scrollIntoView({ block: "center" });
      return;
    }

    const target =
      content.querySelector("[data-autofocus]") ||
      content.querySelector(
        'input:not([type="hidden"]):not([readonly]):not([disabled]), textarea:not([disabled]), select:not([disabled])'
      ) ||
      content.querySelector("button, a[href]");
    target?.focus({ preventScroll: true });
  }

  document.addEventListener(
    "close",
    (event) => {
      if (event.target?.id !== "modal") return;
      document.documentElement.classList.remove("modal-open");
      const active = document.activeElement;
      const focusIsFree =
        !active ||
        active === document.body ||
        active === document.documentElement ||
        event.target.contains(active);
      if (focusIsFree) {
        if (lastTrigger?.isConnected) {
          lastTrigger.focus({ preventScroll: true });
        } else {
          main()?.focus({ preventScroll: true });
        }
      }
      lastTrigger = null;
    },
    true
  );

  document.addEventListener("htmx:beforeRequest", (event) => {
    const source = event.detail.elt;
    if (source?.matches?.('input[name="search"]')) {
      restoreSearchFocus = document.activeElement === source;
    }

    if (event.detail.target === modalContent() && !modal()?.open) {
      lastTrigger = source;
      showLoading();
    }
  });

  document.addEventListener("htmx:afterSwap", (event) => {
    const target = event.detail.target;

    if (target === modalContent()) {
      if (!lastTrigger) return;
      openDialog();
      focusFirstControl();
      return;
    }

    if (target?.id === "main") {
      updateSelection();
      announceResults();
      if (restoreSearchFocus) {
        restoreSearchFocus = false;
        const search = document.querySelector('input[name="search"]');
        if (search && !search.disabled) {
          search.focus({ preventScroll: true });
          search.setSelectionRange?.(search.value.length, search.value.length);
        }
      } else {
        main()?.focus({ preventScroll: true });
      }
      return;
    }

    if (target?.id === "results") {
      updateSelection();
      announceResults();
      return;
    }

    if (target === document.body || target === document.documentElement) {
      lastTrigger = null;
      restoreSearchFocus = false;
      document.documentElement.classList.remove("modal-open");
      syncBodyState();
      updateSelection();
      main()?.focus({ preventScroll: true });
      return;
    }

    updateSelection();
  });

  document.addEventListener("htmx:afterRequest", (event) => {
    const source = event.detail.elt;
    if (source?.matches?.('input[name="search"]') && !event.detail.successful) {
      restoreSearchFocus = false;
    }
  });

  function modalError() {
    const content = modalContent();
    if (!content) return;
    content.innerHTML =
      '<div class="modal-error" role="alert">' +
      '<h2 id="modal-heading">Something went wrong</h2>' +
      '<p class="modal-lede">The request could not be completed. Close this dialog and try again.</p>' +
      '<div class="modal-actions"><button type="button" class="button" data-modal-close>Close</button></div>' +
      "</div>";
    openDialog();
    content.querySelector("[data-modal-close]")?.focus({ preventScroll: true });
  }

  document.body.addEventListener("htmx:responseError", (event) => {
    if (event.detail.target === modalContent()) modalError();
  });

  document.body.addEventListener("htmx:sendError", (event) => {
    if (event.detail.target === modalContent()) modalError();
  });

  document.body.addEventListener("modal-close", closeDialog);

  document.body.addEventListener("list-refresh", () => {
    if (!window.htmx || !document.getElementById("main")) {
      window.location.reload();
      return;
    }
    htmx.ajax("GET", window.location.href, {
      target: "#main",
      select: "#main",
      swap: "innerHTML",
    });
  });

  document.addEventListener("change", (event) => {
    if (event.target.matches("[data-select-all]")) {
      for (const box of rowBoxes()) box.checked = event.target.checked;
    }
    if (
      event.target.matches('[data-select-all], #results input[type="checkbox"][name="ids"]')
    ) {
      updateSelection();
    }
  });

  document.addEventListener("click", (event) => {
    const element = event.target instanceof Element ? event.target : null;
    if (element?.id === "modal") {
      closeDialog();
      return;
    }

    const closer = element?.closest("[data-modal-close]");
    if (closer) {
      event.preventDefault();
      closeDialog();
      return;
    }

    const filterSummary = element?.closest(".filter-menu > summary");
    if (filterSummary) {
      closeFilterMenus(filterSummary.parentElement);
    } else {
      const openFilter = document.querySelector(".filter-menu[open]");
      if (openFilter && (!element || !openFilter.contains(element))) {
        openFilter.open = false;
      }
    }

    const menu = document.querySelector(".mobile-menu[open]");
    if (!menu) return;
    if (!element || !menu.contains(element) || element.closest("a, button")) {
      menu.open = false;
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || modal()?.open) return;

    const filterMenu = document.querySelector(".filter-menu[open]");
    if (filterMenu) {
      filterMenu.open = false;
      filterMenu.querySelector("summary")?.focus();
      return;
    }

    const menu = document.querySelector(".mobile-menu[open]");
    if (!menu) return;
    menu.open = false;
    menu.querySelector("summary")?.focus();
  });

  syncBodyState();
  updateSelection();
})();
