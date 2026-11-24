/* Tests the native token lifecycle with an in-memory Keystore adapter and a mock HTTPS API. */
const assert=require('node:assert/strict');
const path=require('node:path');
const vm=require('node:vm');
const esbuild=require('../web/node_modules/esbuild');

async function test(){
  const output=await esbuild.build({
    entryPoints:[path.join(__dirname,'../web/src/nativeSession.ts')],
    bundle:true,format:'cjs',platform:'node',write:false,logLevel:'silent',
    define:{__CAPZORYX_NATIVE__:'true'},
    external:['@capacitor/core','@aparajita/capacitor-secure-storage']
  });
  const vault=new Map();
  const secure={
    async get(key){return vault.get(key)||null;},
    async set(key,value){vault.set(key,value);},
    async remove(key){return vault.delete(key);}
  };
  let access='test-access-one',refresh='test-refresh-one',rotations=0,expired=false;
  const expiredResponse=()=>Response.json({error:{code:'INVALID_OR_EXPIRED_SESSION'}},{status:401});
  async function mockFetch(url,init){
    assert.equal(new URL(url).origin,'https://capzoryx.vercel.app');
    assert.equal(init.credentials,'omit');assert.equal(init.redirect,'error');
    const route=new URL(url).pathname,headers=new Headers(init.headers);
    assert.equal(headers.get('X-SafeLedger-Client'),'app');
    if(route.startsWith('/api/public/')){
      assert.equal(headers.has('Authorization'),false);
      return Response.json({simulation_only:true});
    }
    if(route==='/api/auth/login')return Response.json({user_id:'sample',simulation_only:true,access_token:access,refresh_token:refresh});
    if(route==='/api/auth/refresh'){
      assert.equal(JSON.parse(init.body).refresh_token,refresh);
      rotations++;await new Promise(resolve=>setTimeout(resolve,5));
      access='test-access-two';refresh='test-refresh-two';expired=false;
      return Response.json({access_token:access,refresh_token:refresh});
    }
    if(route==='/api/auth/logout'){
      assert.equal(JSON.parse(init.body).refresh_token,refresh);
      return Response.json({simulation_only:true});
    }
    return expired||headers.get('Authorization')!=='Bearer '+access?expiredResponse():Response.json({ok:true});
  }
  const module={exports:{}};
  vm.runInNewContext(output.outputFiles[0].text,{
    module,exports:module.exports,location:{origin:'https://app.capzoryx.local'},
    fetch:mockFetch,Response,Headers,URL,AbortSignal,
    require(name){
      if(name==='@capacitor/core')return {Capacitor:{getPlatform:()=>'android'}};
      if(name==='@aparajita/capacitor-secure-storage')return {SecureStorage:secure};
      throw Error('Unexpected test dependency');
    }
  });
  const request=module.exports.nativeFetch;
  assert.equal((await request('/api/session',{})).status,401);
  const login=await (await request('/api/auth/login',{method:'POST'})).json();
  assert.deepEqual(login,{user_id:'sample',simulation_only:true});
  assert.equal(vault.size,1);
  expired=true;
  const responses=await Promise.all(['/api/workspace','/api/account/profile'].map(route=>request(route,{})));
  assert.ok(responses.every(r=>r.ok));assert.equal(rotations,1);
  assert.ok((await request('/api/public/passport/example',{credentials:'omit'})).ok);
  assert.ok((await request('/api/auth/logout',{method:'POST',body:'{}'})).ok);
  assert.equal(vault.size,0);
  assert.equal((await request('/api/session',{})).status,401);
  console.log('Native session: HTTPS origin, credential redaction, single-flight rotation, public lookup and logout passed');
}
test().catch(error=>{console.error(error);process.exitCode=1;});
