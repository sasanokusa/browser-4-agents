const body = document.body;
const all = body ? body.getElementsByTagName('*') : [];
const lim = Math.min(all.length, 20000);
for (let i = 0; i < lim; i++) {
  const el = all[i]; const cs = getComputedStyle(el);
  if (cs.display === 'none' || cs.visibility === 'hidden' || el.hidden ||
      el.getAttribute('aria-hidden') === 'true') el.setAttribute('data-browsr-hidden', '1');
}
const doc = document.cloneNode(true);
doc.querySelectorAll('[data-browsr-hidden],script,style,noscript,template,iframe,svg,canvas,object,embed')
   .forEach(e => e.remove());
doc.querySelectorAll('a[href]').forEach(a => {
  try { a.setAttribute('href', new URL(a.getAttribute('href'), document.baseURI).href); } catch (_) {}
});
let html = null, title = document.title || '', method = 'readability';
try {
  const r = new Readability(doc.cloneNode(true), { charThreshold: 500, keepClasses: false }).parse();
  if (r && r.textContent && r.textContent.trim().length >= args.minChars) {
    html = r.content; title = r.title || title;
  }
} catch (_) {}
if (html === null) {
  method = 'fallback';
  const root = doc.querySelector('main,article,[role=main]') || doc.body;
  if (root) {
    root.querySelectorAll('nav,header,footer,aside,form,button,[role=navigation],[role=banner],' +
      '[role=contentinfo],[class*=cookie i],[id*=cookie i],[class*=consent i],[id*=consent i]')
      .forEach(e => e.remove());
    html = root.innerHTML;
  } else html = '';
}
return { title: title.trim().slice(0, 200), html, method,
         textLen: body ? body.innerText.length : 0,
         textSample: body ? body.innerText.slice(0, 1500) : '' };
