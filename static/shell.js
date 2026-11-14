/* Navigation: aktiven Eintrag markieren, „Mehr“ auf dem Handy öffnen. Läuft auf beiden Seiten. */
(function () {
  "use strict";
  var side = document.querySelector(".sh-side"), more = document.getElementById("sh-more");
  function current() {
    var seg = (location.hash || "#/").replace(/^#\/?/, "").split(/[/?]/)[0];
    return seg === "markt" ? "maerkte" : seg === "wette" ? "wetten" : seg;
  }
  function mark() {
    var cur = current();
    Array.prototype.forEach.call(document.querySelectorAll("[data-nav]"), function (a) {
      var on = a.getAttribute("data-nav") === cur;
      a.classList.toggle("on", on);
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    var inTabs = ["", "maerkte", "prognosen", "paper", "wetten", "kalender"].indexOf(cur) >= 0;
    if (more) more.classList.toggle("on", !inTabs);
  }
  function close() { document.body.classList.remove("sh-open"); if (more) more.setAttribute("aria-expanded", "false"); }
  if (more) more.addEventListener("click", function () {
    var open = !document.body.classList.contains("sh-open");
    document.body.classList.toggle("sh-open", open);
    more.setAttribute("aria-expanded", open ? "true" : "false");
  });
  if (side) side.addEventListener("click", function (e) { if (e.target.closest("a")) close(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
  window.addEventListener("hashchange", function () { mark(); close(); });
  mark();
})();
