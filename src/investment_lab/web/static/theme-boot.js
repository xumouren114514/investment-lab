/* Apply saved appearance before the first paint; loaded synchronously after ui-utils.js. */
(function () {
  'use strict';
  try {
    var UI = window.InvestmentLabUI;
    UI.applyPreferences(UI.readPreferences(window.localStorage));
  } catch (_) { /* Storage can be unavailable; the default theme still applies. */ }
})();
