/* =============================================
   SMORI — Installation Cases: filters + lightbox
   Loaded by sections/smori-cases.liquid and
   sections/smori-case-detail.liquid (guarded so it
   only initialises once per page).
   ============================================= */
(function () {
  if (window.SmoriCases) return;
  window.SmoriCases = true;

  function initFilters(root) {
    var buttons = root.querySelectorAll('[data-case-filter]');
    var cards = root.querySelectorAll('[data-case-category]');
    if (!buttons.length) return;
    buttons.forEach(function (btn) {
      btn.addEventListener('click', function () {
        buttons.forEach(function (b) { b.classList.remove('active'); });
        btn.classList.add('active');
        var filter = btn.getAttribute('data-case-filter');
        cards.forEach(function (card) {
          var match = filter === 'all' || card.getAttribute('data-case-category') === filter;
          card.classList.toggle('is-hidden', !match);
        });
      });
    });
  }

  /* ---- Lightbox ---- */
  var lightbox, imgEl, captionEl, items = [], index = 0;

  function ensureLightbox() {
    if (lightbox) return;
    lightbox = document.createElement('div');
    lightbox.className = 'smori-lightbox';
    lightbox.setAttribute('role', 'dialog');
    lightbox.setAttribute('aria-modal', 'true');
    lightbox.innerHTML =
      '<button type="button" class="smori-lightbox-close" aria-label="Close">&times;</button>' +
      '<button type="button" class="smori-lightbox-prev" aria-label="Previous">&#8249;</button>' +
      '<img alt="">' +
      '<button type="button" class="smori-lightbox-next" aria-label="Next">&#8250;</button>' +
      '<div class="smori-lightbox-caption"></div>';
    document.body.appendChild(lightbox);
    imgEl = lightbox.querySelector('img');
    captionEl = lightbox.querySelector('.smori-lightbox-caption');
    lightbox.querySelector('.smori-lightbox-close').addEventListener('click', close);
    lightbox.querySelector('.smori-lightbox-prev').addEventListener('click', function () { show(index - 1); });
    lightbox.querySelector('.smori-lightbox-next').addEventListener('click', function () { show(index + 1); });
    lightbox.addEventListener('click', function (e) { if (e.target === lightbox) close(); });
    document.addEventListener('keydown', function (e) {
      if (!lightbox.classList.contains('is-open')) return;
      if (e.key === 'Escape') close();
      if (e.key === 'ArrowLeft') show(index - 1);
      if (e.key === 'ArrowRight') show(index + 1);
    });
  }

  function show(i) {
    if (!items.length) return;
    index = (i + items.length) % items.length;
    var item = items[index];
    imgEl.src = item.getAttribute('data-src');
    imgEl.alt = item.getAttribute('data-caption') || '';
    captionEl.textContent = item.getAttribute('data-caption') || '';
    var multiple = items.length > 1;
    lightbox.querySelector('.smori-lightbox-prev').style.display = multiple ? '' : 'none';
    lightbox.querySelector('.smori-lightbox-next').style.display = multiple ? '' : 'none';
  }

  function open(group, start) {
    ensureLightbox();
    items = Array.prototype.slice.call(document.querySelectorAll('[data-smori-lightbox="' + group + '"]'));
    lightbox.classList.add('is-open');
    document.body.style.overflow = 'hidden';
    show(start);
  }

  function close() {
    if (!lightbox) return;
    lightbox.classList.remove('is-open');
    document.body.style.overflow = '';
  }

  function initLightbox() {
    document.querySelectorAll('[data-smori-lightbox]').forEach(function (el) {
      el.addEventListener('click', function (e) {
        e.preventDefault();
        var group = el.getAttribute('data-smori-lightbox');
        var siblings = Array.prototype.slice.call(document.querySelectorAll('[data-smori-lightbox="' + group + '"]'));
        open(group, siblings.indexOf(el));
      });
    });
  }

  function init() {
    document.querySelectorAll('[data-smori-cases]').forEach(initFilters);
    initLightbox();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  /* Theme editor: re-init when a section is re-rendered */
  document.addEventListener('shopify:section:load', init);
})();
