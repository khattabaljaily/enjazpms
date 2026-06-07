(function (){
    const API = '/ai/advices/';
    const FETCH_TIMEOUT = 10000;
    const ROTATE_MS = 5000;

    function el(id){ return document.getElementById(id); }

    let advices = [];
    let idx = 0;
    let timer = null;

    function render(){
        const container = el('aiAdviceText');
        if(!container) return;
        if(advices.length===0){ container.textContent = 'لا توجد نصائح حالياً.'; return; }
        container.textContent = advices[idx];
    }

    function next(){ idx = (idx+1) % advices.length; render(); }
    function prev(){ idx = (idx-1+advices.length) % advices.length; render(); }

    function startAuto(){ stopAuto(); timer = setInterval(next, ROTATE_MS); }
    function stopAuto(){ if(timer){ clearInterval(timer); timer=null; } }

    function setupControls(){
        const nextBtn = el('aiNext');
        const prevBtn = el('aiPrev');
        const widget = el('aiAdvicesWidget');
        if(nextBtn) nextBtn.addEventListener('click', ()=>{ next(); startAuto(); });
        if(prevBtn) prevBtn.addEventListener('click', ()=>{ prev(); startAuto(); });
        if(widget){
            widget.addEventListener('mouseenter', stopAuto);
            widget.addEventListener('mouseleave', startAuto);
        }
    }

    function fetchAdvices(){
        fetch(API, {credentials: 'same-origin'})
            .then(resp=>resp.json())
            .then(data=>{
                if(data && Array.isArray(data.advices)){
                    // randomize order
                    advices = data.advices.slice();
                    for(let i=advices.length-1;i>0;i--){
                        const j = Math.floor(Math.random()*(i+1));
                        [advices[i], advices[j]] = [advices[j], advices[i]];
                    }
                    idx = 0;
                    render();
                    startAuto();
                }
            })
            .catch(()=>{
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
