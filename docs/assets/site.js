// Copy buttons: data-copy names the id of the <pre> to copy.
document.querySelectorAll("[data-copy]").forEach(function (btn) {
  btn.addEventListener("click", function () {
    var el = document.getElementById(btn.getAttribute("data-copy"));
    if (!el) return;
    var done = function (label) {
      btn.textContent = label;
      setTimeout(function () { btn.textContent = "Copy"; }, 1600);
    };
    var select = function () {
      var range = document.createRange();
      range.selectNodeContents(el);
      var sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      done("Selected");
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(el.textContent).then(function () { done("Copied"); }, select);
    } else {
      select();
    }
  });
});

// Leaderboard filters: each chip shows one dataset's rows, or all of them.
var chips = document.querySelectorAll(".chip[data-filter]");
chips.forEach(function (chip) {
  chip.addEventListener("click", function () {
    var want = chip.getAttribute("data-filter");
    chips.forEach(function (c) { c.setAttribute("aria-pressed", String(c === chip)); });
    document.querySelectorAll("table.board tbody[data-set]").forEach(function (group) {
      group.hidden = want !== "all" && group.getAttribute("data-set") !== want;
    });
  });
});

// Enhance ordinary anchor links into tabs. Without JavaScript, both views remain
// readable. Hash links, reloads and browser history reveal the destination view.
var boardTabs = Array.from(document.querySelectorAll("[data-tab]"));
var boardPanels = document.querySelectorAll("[data-tab-panel]");
if (boardTabs.length) {
  document.querySelector(".board-tabs").setAttribute("role", "tablist");
  boardTabs.forEach(function (tab) {
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", tab.getAttribute("data-tab"));
    tab.addEventListener("click", function (event) {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      showBoardPanel(tab.getAttribute("data-tab"));
      var hash = tab.getAttribute("href");
      if (window.location.hash !== hash) history.pushState(null, "", hash);
    });
    tab.addEventListener("keydown", function (event) {
      var index = boardTabs.indexOf(tab);
      if (event.key === "ArrowRight") index = (index + 1) % boardTabs.length;
      else if (event.key === "ArrowLeft") index = (index + boardTabs.length - 1) % boardTabs.length;
      else if (event.key === "Home") index = 0;
      else if (event.key === "End") index = boardTabs.length - 1;
      else if (event.key === " ") { event.preventDefault(); tab.click(); return; }
      else return;
      event.preventDefault();
      boardTabs[index].focus();
      boardTabs[index].click();
    });
  });
  boardPanels.forEach(function (panel) {
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("tabindex", "0");
  });

  function showBoardPanel(id) {
    boardTabs.forEach(function (tab) {
      var active = tab.getAttribute("data-tab") === id;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    boardPanels.forEach(function (panel) { panel.hidden = panel.id !== id; });
  }

  function revealBoardTarget(hash) {
    var target = document.getElementById(hash.slice(1));
    var panel = target && target.closest("[data-tab-panel]");
    if (panel) showBoardPanel(panel.id);
    else if (!hash) showBoardPanel("results");
    if (target && target.id === "jev-tool-usage") target.open = true;
    return target;
  }

  document.querySelectorAll('a[href^="#"]').forEach(function (link) {
    link.addEventListener("click", function (event) {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      revealBoardTarget(link.getAttribute("href"));
    });
  });
  window.addEventListener("hashchange", function () {
    var target = revealBoardTarget(window.location.hash);
    if (target) target.scrollIntoView();
  });
  showBoardPanel("results");
  var initialTarget = revealBoardTarget(window.location.hash);
  if (initialTarget) requestAnimationFrame(function () { initialTarget.scrollIntoView(); });
}
