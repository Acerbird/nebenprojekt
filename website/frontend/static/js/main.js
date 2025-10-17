// frontend/static/js/main.js
// Small enhancement and accessible hover cursor
document.documentElement.lang = 'de';

// accessible focus styles for keyboard users
document.addEventListener('keyup', (e) => {
  if (e.key === 'Tab') {
    document.body.classList.add('user-is-tabbing');
  }
});

// optional: fetch health when page loads (example usage)
async function checkHealth(){
  try{
    const r = await fetch('/health');
    const j = await r.json();
    if (j.status !== 'ok') console.warn('Backend health not ok', j);
  }catch(e){
    console.warn('Cannot reach backend', e);
  }
}
checkHealth();
