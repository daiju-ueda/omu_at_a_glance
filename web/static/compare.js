(function () {
  var bar = document.getElementById("compare-bar");
  if (!bar) return;
  var names = document.getElementById("compare-names");
  var go = document.getElementById("compare-go");

  function selected() {
    return Array.prototype.slice.call(
      document.querySelectorAll("input.cmp:checked"));
  }

  function update() {
    var sel = selected();
    bar.hidden = sel.length === 0;
    names.textContent = sel.map(function (el) {
      return el.dataset.name;
    }).join("、");
    go.disabled = sel.length < 2 || sel.length > 4;
    go.textContent = "比較する（" + sel.length + "人）";
  }

  document.addEventListener("change", function (e) {
    if (e.target.classList && e.target.classList.contains("cmp")) update();
  });

  go.addEventListener("click", function () {
    var ids = selected().map(function (el) { return el.dataset.id; });
    window.location.href = "/compare?ids=" + ids.join(",");
  });
})();
