document.getElementById('print-button')?.addEventListener('click',()=>window.print());
document.querySelectorAll('form[method="post"]').forEach(form=>form.addEventListener('submit',()=>{form.querySelectorAll('button').forEach(button=>{button.disabled=true;});}));
const terminalClock=document.getElementById('terminal-clock');
if(terminalClock){const zone=terminalClock.parentElement.textContent.split('/')[0].trim();const updateClock=()=>{try{terminalClock.textContent=new Intl.DateTimeFormat('en-GB',{timeZone:zone,hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date());}catch{terminalClock.textContent='LOCAL TIME';}};updateClock();setInterval(updateClock,1000);}
// Passwords stay hidden until the user explicitly chooses to show them.
document.querySelectorAll('input[type="password"]').forEach((input,index)=>{
    const wrapper=document.createElement('span');
    wrapper.className='password-field';
    input.parentNode.insertBefore(wrapper,input);
    wrapper.appendChild(input);
    if(!input.id) input.id=`password-${index}`;
    const toggle=document.createElement('button');
    toggle.type='button';
    toggle.className='password-toggle';
    toggle.textContent='Show';
    toggle.setAttribute('aria-label','Show password');
    toggle.setAttribute('aria-controls',input.id);
    toggle.setAttribute('aria-pressed','false');
    toggle.addEventListener('click',()=>{
        const visible=input.type==='password';
        input.type=visible?'text':'password';
        toggle.textContent=visible?'Hide':'Show';
        toggle.setAttribute('aria-label',visible?'Hide password':'Show password');
        toggle.setAttribute('aria-pressed',String(visible));
    });
    wrapper.appendChild(toggle);
});
