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
    document.querySelectorAll("tbody[data-set]").forEach(function (group) {
      group.hidden = want !== "all" && group.getAttribute("data-set") !== want;
    });
  });
});

// Open the separate JevGPT tool table when following a row's link.
function openJevTools() {
  var details = document.getElementById("jev-tool-usage");
  if (details) details.open = true;
}
document.querySelectorAll('a[href="#jev-tool-usage"]').forEach(function (link) {
  link.addEventListener("click", openJevTools);
});
if (window.location.hash === "#jev-tool-usage") openJevTools();
