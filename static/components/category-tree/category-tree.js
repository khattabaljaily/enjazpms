/* Minimal, dependency-free category tree component
   Usage:
     - Include CSS and this JS
     - Call `initCategoryTrees()` or `initCategoryTree(container, options)`
     - Optionally provide `data-json` attribute containing JSON or call `renderFromJson`
*/
(function(){
  function createNode(item){
    const li = document.createElement('li'); li.className = 'ct-item';
    const row = document.createElement('div'); row.className = 'ct-row'; row.tabIndex = 0; row.setAttribute('role','treeitem');
    const toggle = document.createElement('button'); toggle.className = 'ct-toggle'; toggle.setAttribute('aria-hidden','false');
    const icon = document.createElement('span'); icon.className = 'ct-toggle-icon'; icon.innerHTML = '\u25B6';
    toggle.appendChild(icon);
    const label = document.createElement('div'); label.className = 'ct-label'; label.textContent = item.name || '—';
    row.appendChild(toggle); row.appendChild(label);
    if(item.meta){ const meta = document.createElement('div'); meta.className='ct-meta'; meta.textContent = item.meta; row.appendChild(meta); }
    li.appendChild(row);
    if(item.children && item.children.length){
      const children = document.createElement('ul'); children.className='ct-children'; children.setAttribute('role','group');
      item.children.forEach(ch=> children.appendChild(createNode(ch)));
      li.appendChild(children);
      row.setAttribute('aria-expanded','false');
      toggle.addEventListener('click', e=>{
        const expanded = row.getAttribute('aria-expanded') === 'true';
        row.setAttribute('aria-expanded', String(!expanded));
        children.classList.toggle('expanded', !expanded);
      });
      // keyboard
      row.addEventListener('keydown', e=>{
        if(e.key === 'Enter' || e.key === ' '){ toggle.click(); e.preventDefault(); }
        if(e.key === 'ArrowRight'){ if(row.getAttribute('aria-expanded')==='false') toggle.click(); }
        if(e.key === 'ArrowLeft'){ if(row.getAttribute('aria-expanded')==='true') toggle.click(); }
      });
    } else {
      // leaf
      const placeholder = document.createElement('div'); placeholder.className='ct-toggle'; placeholder.style.visibility='hidden'; placeholder.innerHTML='';
      row.insertBefore(placeholder, row.firstChild);
    }
    return li;
  }

  function renderFromJson(container, json){
    container.innerHTML = '';
    const root = document.createElement('div'); root.className='ct-root';
    const ul = document.createElement('ul'); ul.className='ct-list'; ul.setAttribute('role','tree');
    (json || []).forEach(item=> ul.appendChild(createNode(item)));
    root.appendChild(ul);
    container.appendChild(root);
  }

  function initCategoryTree(node){
    if(typeof node === 'string') node = document.querySelector(node);
    if(!node) return;
    // if data-json attribute present, parse and render
    const jsonAttr = node.getAttribute('data-json');
    if(jsonAttr){ try{ const data = JSON.parse(jsonAttr); renderFromJson(node, data); }catch(e){ console.error('Invalid JSON in data-json', e); }}
    // allow markup-based trees: find nested <ul>
    const existingUL = node.querySelector('ul');
    if(existingUL){ // wire up toggles for existing markup
      node.querySelectorAll('li').forEach(li=>{
        const childUL = li.querySelector(':scope > ul');
        const row = li.querySelector(':scope > div') || li;
        if(!row) return;
        row.tabIndex = 0; row.setAttribute('role','treeitem');
        if(childUL){ childUL.classList.remove('expanded'); childUL.classList.add('ct-children'); row.setAttribute('aria-expanded','false');
          const toggle = document.createElement('button'); toggle.className='ct-toggle'; toggle.innerHTML='<span class="ct-toggle-icon">\u25B6</span>';
          row.insertBefore(toggle, row.firstChild);
          toggle.addEventListener('click', ()=>{ const expanded = row.getAttribute('aria-expanded')==='true'; row.setAttribute('aria-expanded', String(!expanded)); childUL.classList.toggle('expanded', !expanded); });
        } else { const placeholder = document.createElement('div'); placeholder.className='ct-toggle'; placeholder.style.visibility='hidden'; row.insertBefore(placeholder, row.firstChild); }
      });
    }
  }

  function initCategoryTrees(selector){
    const nodes = document.querySelectorAll(selector || '.category-tree');
    nodes.forEach(n=> initCategoryTree(n));
  }

  // expose API
  window.CategoryTree = { init: initCategoryTree, initAll: initCategoryTrees, renderFromJson };
  document.addEventListener('DOMContentLoaded', ()=> initCategoryTrees('.category-tree'));
})();
