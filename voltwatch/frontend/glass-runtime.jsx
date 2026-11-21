import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Glass} from '@samasante/liquid-glass';

function Backing() {
  const [dark, setDark] = useState(document.documentElement.dataset.theme === 'dark');
  useEffect(() => {
    const update = () => setDark(document.documentElement.dataset.theme === 'dark');
    document.addEventListener('voltwatch:theme', update);
    return () => document.removeEventListener('voltwatch:theme', update);
  }, []);
  return <Glass aria-hidden="true" optics={{strength:.08,depth:.18,curvature:.04,dispersion:.12,bend:.25,frost:10,sheen:.25,specular:.4,glow:0}} style={{display:'block',width:'100%',height:'100%',borderRadius:8,background:dark?'rgba(22,24,28,.84)':'rgba(255,255,255,.9)'}}><span style={{display:'block',height:'100%'}}/></Glass>;
}

const mounts = new Map();
let queued = false, nextId = 0;
function scan() {
  queued = false;
  for (const [target, mount] of mounts) {
    if (!target.isConnected || !target.contains(mount.host)) {
      mount.root.unmount();
      mounts.delete(target);
      target.removeAttribute('data-glass-ready');
    }
  }
  document.querySelectorAll('[data-glass]').forEach(target => {
    if (mounts.has(target)) return;
    const host = document.createElement('div');
    host.className = 'glass-backing';
    host.setAttribute('aria-hidden', 'true');
    target.prepend(host);
    const root = createRoot(host, {identifierPrefix:`volt-glass-${nextId++}-`});
    root.render(<Backing/>);
    target.setAttribute('data-glass-ready', '');
    mounts.set(target, {host, root});
  });
}
scan();
new MutationObserver(() => {
  if (!queued) { queued = true; requestAnimationFrame(scan); }
}).observe(document.body, {childList:true, subtree:true});
window.VoltGlass = {library:'@samasante/liquid-glass', version:'0.1.1'};
