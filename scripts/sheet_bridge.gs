/**
 * Globex calendar sheet bridge — deploy INSIDE the "Globex content calendar" sheet.
 *
 * Gives the automation two-way access to the "Exact Caption" column without any
 * Google credentials in the app:
 *   GET  ?secret=...&title=<Key Feature/Theme>   -> {"caption": "...", "found": true}
 *   POST {"secret": ..., "title": ..., "caption": ...} -> {"ok": true}
 *
 * SETUP (3 minutes, once):
 *   1. Open the sheet -> Extensions -> Apps Script. Delete any starter code,
 *      paste this whole file.
 *   2. Change SECRET below to a long random string.
 *   3. Deploy -> New deployment -> type "Web app":
 *        Execute as: Me    |    Who has access: Anyone
 *      Copy the Web app URL.
 *   4. On Railway (and local .env) set:
 *        SHEET_WEBAPP_URL=<the web app URL>
 *        SHEET_WEBAPP_SECRET=<the same secret>
 *
 * Row matching is by the "Key Feature/Theme" column, which equals each calendar
 * entry's title in the automation. Redeploy ("Manage deployments" -> edit ->
 * new version) after any change to this file.
 */

var SECRET = "CHANGE-ME-to-a-long-random-string";

var HEADER_ROW = 4;            // the row holding "Wk / Date / Category / ..."
var TITLE_HEADER = "Key Feature/Theme";
var CAPTION_HEADER = "Exact Caption"; // matched by prefix — the real header is longer

function _sheet() {
  return SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
}

function _columns(sheet) {
  var headers = sheet.getRange(HEADER_ROW, 1, 1, sheet.getLastColumn()).getValues()[0];
  var title = -1, caption = -1;
  for (var i = 0; i < headers.length; i++) {
    var h = String(headers[i]).trim();
    if (h === TITLE_HEADER) title = i + 1;
    if (h.indexOf(CAPTION_HEADER) === 0) caption = i + 1;
  }
  return { title: title, caption: caption };
}

function _findRow(sheet, cols, wanted) {
  var last = sheet.getLastRow();
  var titles = sheet.getRange(HEADER_ROW + 1, cols.title, last - HEADER_ROW, 1).getValues();
  var target = String(wanted).trim().toLowerCase();
  for (var i = 0; i < titles.length; i++) {
    if (String(titles[i][0]).trim().toLowerCase() === target) {
      return HEADER_ROW + 1 + i;
    }
  }
  return -1;
}

function _json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function doGet(e) {
  var p = (e && e.parameter) || {};
  if (p.secret !== SECRET) return _json({ error: "unauthorized" });
  var sheet = _sheet();
  var cols = _columns(sheet);
  if (cols.title < 0 || cols.caption < 0) return _json({ error: "headers not found" });
  var row = _findRow(sheet, cols, p.title || "");
  if (row < 0) return _json({ caption: "", found: false });
  var caption = String(sheet.getRange(row, cols.caption).getValue() || "");
  return _json({ caption: caption, found: true });
}

function doPost(e) {
  var body = {};
  try { body = JSON.parse(e.postData.contents || "{}"); } catch (err) {}
  if (body.secret !== SECRET) return _json({ error: "unauthorized" });
  var sheet = _sheet();
  var cols = _columns(sheet);
  if (cols.title < 0 || cols.caption < 0) return _json({ error: "headers not found" });
  var row = _findRow(sheet, cols, body.title || "");
  if (row < 0) return _json({ ok: false, found: false });
  sheet.getRange(row, cols.caption).setValue(String(body.caption || ""));
  return _json({ ok: true });
}
