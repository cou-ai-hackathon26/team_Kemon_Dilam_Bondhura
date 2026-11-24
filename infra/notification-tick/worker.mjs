/** Free scheduled Worker. Bind API_ORIGIN and the secret CAPZORYX_TICK_SECRET. */
export async function runTick(origin, secret) {
  const target = new URL(origin);
  if (target.protocol !== 'https:' || target.username || target.password || target.pathname !== '/' || target.search || target.hash || !secret || secret.length < 32) {
    throw new Error('Invalid notification scheduler configuration');
  }
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(secret), {name:'HMAC',hash:'SHA-256'},false,['sign']);
  for (let batch = 0; batch < 40; batch++) {
    const timestamp = String(Math.floor(Date.now()/1000));
    const proof = await crypto.subtle.sign('HMAC',key,new TextEncoder().encode(timestamp+'.POST./api/internal/tick'));
    const signature = Array.from(new Uint8Array(proof),n=>n.toString(16).padStart(2,'0')).join('');
    const response = await fetch(new URL('/api/internal/tick',target),{
      method:'POST', body:'{}', redirect:'error', headers:{'Content-Type':'application/json','X-Capzoryx-Timestamp':timestamp,'X-Capzoryx-Signature':signature},
      signal:AbortSignal.timeout(85000)
    });
    if (!response.ok) throw new Error('Notification scheduler returned HTTP '+response.status);
    if (!(await response.json()).more) return;
  }
  throw new Error('Additional workspaces are pending; resume on the next tick');
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(runTick(env.API_ORIGIN,env.CAPZORYX_TICK_SECRET));
  }
};
