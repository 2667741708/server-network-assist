const root=document.documentElement;
const syncTheme=()=>root.classList.toggle('dark',root.dataset.theme==='dark');
try {const theme=localStorage.getItem('whm-projects-theme');if(theme)root.dataset.theme=theme;}catch{}
syncTheme();
document.querySelector('#theme').addEventListener('click',()=>{root.dataset.theme=root.dataset.theme==='dark'?'light':'dark';syncTheme();try{localStorage.setItem('whm-projects-theme',root.dataset.theme);}catch{}});
const form=document.querySelector('.filters');
if(form){
  form.addEventListener('submit',event=>event.preventDefault());
  const search=document.querySelector('#search'),category=document.querySelector('#category'),kind=document.querySelector('#kind'),forks=document.querySelector('#include-forks');
  const params=new URLSearchParams(location.search);
  search.value=params.get('q')||'';category.value=params.get('category')||'';kind.value=params.get('kind')||'';forks.checked=params.get('forks')!=='0';
  const cards=[...document.querySelectorAll('.project-card')];
  const update=()=>{let count=0;const query=search.value.trim().toLowerCase();for(const card of cards){const show=(!query||card.textContent.toLowerCase().includes(query))&&(!category.value||card.dataset.category===category.value)&&(!kind.value||card.dataset.kind===kind.value)&&(forks.checked||card.dataset.fork!=='true');card.hidden=!show;if(show)count++;}document.querySelector('#result-count').textContent=count+' 个项目';document.querySelector('#empty').hidden=count!==0;const next=new URLSearchParams();if(search.value)next.set('q',search.value);if(category.value)next.set('category',category.value);if(kind.value)next.set('kind',kind.value);if(!forks.checked)next.set('forks','0');try{history.replaceState(null,'',location.pathname+(next.size?'?'+next:'')+location.hash);}catch{}};
  form.addEventListener('input',update);form.addEventListener('change',update);update();
}
