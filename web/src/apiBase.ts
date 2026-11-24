/** Website and installed PWA use the same-origin rewrite. The packaged Android
 * WebView can send credentials only to our canonical HTTPS API. */
declare const __CAPZORYX_NATIVE__:boolean;
export const nativeBuild = typeof __CAPZORYX_NATIVE__ !== 'undefined' && __CAPZORYX_NATIVE__;
/** The Android app, or the website installed to the home screen: both get the app layout and offline start. */
export const installedApp = nativeBuild || (typeof window!=='undefined' && (window.matchMedia?.('(display-mode: standalone)').matches || (navigator as Navigator&{standalone?:boolean}).standalone===true));
/** A signed, published Android release can read payment SMS on the phone. */
export const ANDROID_APP_URL = import.meta.env?.VITE_CAPZORYX_ANDROID_APK_URL || '';
export const androidPhone = typeof navigator!=='undefined' && /Android/i.test(navigator.userAgent);
export const publicOrigin = () => nativeBuild ? 'https://capzoryx.vercel.app' : location.origin;

export async function apiFetch(path:string, init:RequestInit={}):Promise<Response>{
  if(!nativeBuild)return fetch(path,init);
  if(init.method==='POST'&&['/api/auth/logout','/api/account/join','/api/account/switch','/api/account/workspaces','/api/account/leave','/api/account/workspaces/clear'].includes(path)){
    await (await import('./nativePush')).stopNativePush();
  }
  const {nativeFetch}=await import('./nativeSession');
  return nativeFetch(path,init);
}
