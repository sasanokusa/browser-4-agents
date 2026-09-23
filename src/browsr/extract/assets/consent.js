() => {
  const W = [/^(accept|agree|allow)( all)?( cookies)?$/i, /^(ok|got it|i agree|i accept)$/i,
             /^(すべて)?(同意|許可|受け入れ|承諾)(する|します|る)?$/, /^(alle )?akzeptieren$/i];
  const roots = document.querySelectorAll('[id*=cookie i],[class*=cookie i],[id*=consent i],' +
    '[class*=consent i],[id*=gdpr i],[class*=gdpr i],[aria-modal=true],[role=dialog]');
  for (const r of roots)
    for (const b of r.querySelectorAll('button,a,[role=button],input[type=button],input[type=submit]')) {
      const t = (b.innerText || b.value || '').trim();
      if (t.length <= 30 && W.some(w => w.test(t))) { b.click(); return true; }
    }
  return false;
}
