/* -------------------------------------------------------
   Flip cards — Before/After (overlay + blur + lock)
------------------------------------------------------- */
(function () {
  const cards = [...document.querySelectorAll('[data-flip]')];
  if (!cards.length) return;

  // Overlay
  let overlay = document.querySelector('.flip-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.className = 'flip-overlay';
    document.body.appendChild(overlay);
  }

  let openCard = null;
  let lastTrigger = null;

  const lock = () => document.body.classList.add('is-locked');
  const unlock = () => document.body.classList.remove('is-locked');

  function open(card, trigger) {
    if (openCard && openCard !== card) closeOpen({ restoreFocus:false });

    openCard = card;
    lastTrigger = trigger || card;

    card.classList.add('is-open');
    card.setAttribute('aria-expanded','true');
    overlay.classList.add('is-visible');
    lock();

    const back = card.querySelector('.flip-back');
    if (back) { back.setAttribute('tabindex','-1'); back.focus(); }
  }

  function closeOpen({ restoreFocus=true } = {}) {
    if (!openCard) return;
    openCard.classList.remove('is-open');
    openCard.setAttribute('aria-expanded','false');
    overlay.classList.remove('is-visible');
    unlock();

    if (restoreFocus && lastTrigger) {
      try { lastTrigger.focus(); } catch (e) {}
    }
    openCard = null;
    lastTrigger = null;
  }

  // Bind
  cards.forEach(card => {
    const front = card.querySelector('[data-flip-front]');
    const plus  = card.querySelector('.poster-plus');
    const close = card.querySelector('.poster-close');
    const back  = card.querySelector('.flip-back');

    if (front) front.addEventListener('click', () => open(card, front));
    if (plus)  plus.addEventListener('click', (e)=>{ e.stopPropagation(); open(card, plus); });
    if (back)  back.addEventListener('click', (e)=>{ if (!e.target.closest('.no-close')) closeOpen(); });
    if (close) close.addEventListener('click', (e)=>{ e.stopPropagation(); closeOpen(); });
  });

  // Close with Esc
  document.addEventListener('keydown', (e)=>{
    if (e.key === 'Escape') closeOpen();
  });

  // Overlay click closes
  overlay.addEventListener('click', ()=> closeOpen());

  // Any scroll while open -> close
  let lastY = window.scrollY;
  window.addEventListener('scroll', ()=>{
    if (!openCard) { lastY = window.scrollY; return; }
    if (Math.abs(window.scrollY - lastY) > 0) closeOpen();
    lastY = window.scrollY;
  }, { passive:true });
})();

/* -------------------------------------------------------
   Flip cards — How it works (no overlay)
------------------------------------------------------- */
(function () {
  const cards = [...document.querySelectorAll('[data-flip-lite]')];
  if (!cards.length) return;

  let openCard = null;

  function open(card){
    if (openCard && openCard !== card){
      openCard.classList.remove('is-open');
      openCard.setAttribute('aria-expanded','false');
    }
    openCard = card;
    card.classList.add('is-open');
    card.setAttribute('aria-expanded','true');
    const back = card.querySelector('.flip-back');
    if (back){ back.setAttribute('tabindex','-1'); back.focus(); }
  }
  function close(card){
    (card || openCard)?.classList.remove('is-open');
    (card || openCard)?.setAttribute('aria-expanded','false');
    if (!card) openCard = null;
  }

  cards.forEach(card=>{
    const front = card.querySelector('[data-flip-front]');
    const plus  = card.querySelector('.poster-plus');
    const closeBtn = card.querySelector('.poster-close');
    const back  = card.querySelector('.flip-back');

    if (front) front.addEventListener('click', ()=> open(card));
    if (plus)  plus.addEventListener('click',(e)=>{ e.stopPropagation(); open(card); });
    if (back)  back.addEventListener('click',(e)=>{ if (!e.target.closest('.no-close')) close(card); });
    if (closeBtn) closeBtn.addEventListener('click',(e)=>{ e.stopPropagation(); close(card); });
  });

  document.addEventListener('keydown',(e)=>{ if (e.key==='Escape') close(); });
})();

/* -------------------------------------------------------
   ROI calculator (live) — no “based on …” line
------------------------------------------------------- */
(function (){
  const elR = document.getElementById('range_reviews');
  const elM = document.getElementById('range_min');
  const elH = document.getElementById('range_rate');
  const outR= document.getElementById('out_reviews');
  const outM= document.getElementById('out_min');
  const outH= document.getElementById('out_rate');
  const money = document.getElementById('kpi_money');
  const time  = document.getElementById('kpi_time');
  if (!(elR && elM && elH)) return;

  const f0 = n => new Intl.NumberFormat('en-US',{maximumFractionDigits:0}).format(n);
  const f1 = n => new Intl.NumberFormat('en-US',{maximumFractionDigits:1}).format(n);

  function compute(){
    const r = +elR.value, m = +elM.value, h = +elH.value;
    outR.textContent=r; outM.textContent=m; outH.textContent=h;

    // Approximate weekly hours saved
    let hrs = (r*m/60)*7 + 3.0 - 2.0;
    if (hrs < 0) hrs = 0;

    const eurosMonth = hrs*h*4.33;
    money.textContent = `${f0(eurosMonth)} €`;
    time.textContent  = `${f1(hrs)} hrs/week saved`;
  }
  [elR,elM,elH].forEach(i=> i.addEventListener('input', compute));
  compute();
})();

/* -------------------------------------------------------
   Subtle hero parallax
------------------------------------------------------- */
(function(){
  const bg=document.querySelector('.hero__bg');
  if(!bg) return;
  window.addEventListener('scroll',()=>{
    const y=Math.min(120, window.scrollY/6);
    bg.style.transform=`translateY(${y}px)`;
  },{passive:true});
})();