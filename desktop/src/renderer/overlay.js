const selection = document.getElementById('selection'); const error = document.getElementById('error'); let start = null;
function draw(a,b){const x=Math.min(a.x,b.x),y=Math.min(a.y,b.y),w=Math.abs(a.x-b.x),h=Math.abs(a.y-b.y);Object.assign(selection.style,{display:'block',left:`${x}px`,top:`${y}px`,width:`${w}px`,height:`${h}px`});return{x,y,width:w,height:h}}
addEventListener('mousedown',e=>{if(e.button!==0)return;start={x:e.clientX,y:e.clientY};error.hidden=true;draw(start,start)});
addEventListener('mousemove',e=>{if(start)draw(start,{x:e.clientX,y:e.clientY})});
addEventListener('mouseup',e=>{if(!start||e.button!==0)return;const rectangle=draw(start,{x:e.clientX,y:e.clientY});start=null;window.visualCopilot.completeSelection({displayId:'pending',rectangle})});
addEventListener('keydown',e=>{if(e.key==='Escape')window.visualCopilot.cancel()});
window.visualCopilot.onError(message=>{error.textContent=message;error.hidden=false;selection.style.display='none'});
