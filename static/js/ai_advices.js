(function (){
    const API = '/ai/advices/';
    const ROTATE_MS = 5000;

    function el(id){ return document.getElementById(id); }

    let advices = [];
    let idx = 0;
    let timer = null;

    function openAdviceModal(message){
        const modal = el('aiAdviceModal');
        const body = el('aiAdviceModalBody');
        if(!modal || !body) return;
        stopAuto();
        body.textContent = message || '';
        modal.classList.add('is-open');
        modal.setAttribute('aria-hidden', 'false');
        document.body.style.overflow = 'hidden';
        const adviceText = el('aiAdviceText');
        if(adviceText){ adviceText.setAttribute('aria-expanded', 'true'); }
    }

    function closeAdviceModal(){
        const modal = el('aiAdviceModal');
        if(!modal) return;
        modal.classList.remove('is-open');
        modal.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = '';
        const adviceText = el('aiAdviceText');
        if(adviceText){ adviceText.setAttribute('aria-expanded', 'false'); }
        startAuto();
    }

    function render(){
        const container = el('aiAdviceText');
        if(!container) return;
        if(advices.length===0){ container.textContent = 'لا توجد نصائح حالياً.'; return; }
        container.textContent = advices[idx];
        container.setAttribute('data-advice-index', idx);
    }

    function next(){ if(advices.length===0) return; idx = (idx+1) % advices.length; render(); }
    function prev(){ if(advices.length===0) return; idx = (idx-1+advices.length) % advices.length; render(); }

    function startAuto(){ stopAuto(); timer = setInterval(next, ROTATE_MS); }
    function stopAuto(){ if(timer){ clearInterval(timer); timer=null; } }

    function setupControls(){
        const nextBtn = el('aiNext');
        const prevBtn = el('aiPrev');
        const widget = el('aiAdvicesWidget');
        const adviceText = el('aiAdviceText');
        const modal = el('aiAdviceModal');
        const modalClose = el('aiAdviceModalClose');

        if(nextBtn) nextBtn.addEventListener('click', ()=>{ next(); startAuto(); });
        if(prevBtn) prevBtn.addEventListener('click', ()=>{ prev(); startAuto(); });
        if(widget){
            widget.addEventListener('mouseenter', stopAuto);
            widget.addEventListener('mouseleave', startAuto);
        }
        if(adviceText){
            adviceText.setAttribute('role', 'button');
            adviceText.setAttribute('tabindex', '0');
            adviceText.setAttribute('aria-expanded', 'false');
            adviceText.addEventListener('click', ()=>{ if(advices.length) openAdviceModal(advices[idx]); });
            adviceText.addEventListener('keydown', (event)=>{
                if(event.key === 'Enter' || event.key === ' '){
                    event.preventDefault();
                    if(advices.length) openAdviceModal(advices[idx]);
                }
            });
        }
        if(modal){
            modal.addEventListener('click', (event)=>{
                if(event.target === modal) closeAdviceModal();
            });
        }
        if(modalClose){ modalClose.addEventListener('click', closeAdviceModal); }
        document.addEventListener('keydown', (event)=>{ if(event.key === 'Escape'){ closeAdviceModal(); } });
    }

    function fetchAdvices(){
        fetch(API, { credentials: 'same-origin' })
            .then(resp => resp.json())
            .then(data => {
                if(data && Array.isArray(data.advices) && data.advices.length){
                    advices = data.advices.slice();
                    for(let i = advices.length - 1; i > 0; i--){
                        const j = Math.floor(Math.random() * (i + 1));
                        [advices[i], advices[j]] = [advices[j], advices[i]];
                    }
                    idx = 0;
                    render();
                    startAuto();
                    return;
                }
                advices = ['لا توجد نصائح حالياً.'];
                render();
            })
            .catch(() => {
                advices = ['تعذّر جلب النصائح حالياً.'];
                render();
            });
    }

    document.addEventListener('DOMContentLoaded', function(){
        if(!document.getElementById('aiAdvicesWidget')) return;
        setupControls();
        fetchAdvices();
    });
})();
