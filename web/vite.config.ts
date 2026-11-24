import {readFileSync} from 'node:fs';
import {defineConfig,loadEnv} from 'vite';
export default defineConfig(({mode})=>{
  const env=loadEnv(mode,'.','SAFELEDGER_DEV_');
  return {
    define:{__CAPZORYX_NATIVE__:JSON.stringify(mode==='native')},
    server:{proxy:{'/api':env.SAFELEDGER_DEV_API_URL||'http://127.0.0.1:8000'}},
    build:{outDir:mode==='native'?'dist-native':'dist'},
    plugins:mode==='native'?[{
      name:'native-content-security-policy',
      generateBundle(){
        // Capacitor omits hidden directories when copying the web bundle.
        this.emitFile({type:'asset',fileName:'passport-keys.json',source:readFileSync(
          new URL('./public/.well-known/capzoryx-passport-key.json',import.meta.url)
        )});
      },
      transformIndexHtml(){
        return [{
          tag:'meta',injectTo:'head-prepend',
          attrs:{'http-equiv':'Content-Security-Policy',content:"default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; worker-src 'self'; connect-src 'self' https://capzoryx.vercel.app; img-src 'self' data:; media-src 'self' blob:; frame-src 'none'; base-uri 'self'; form-action 'none'; object-src 'none'"}
        }];
      }
    }]:[]
  };
});
