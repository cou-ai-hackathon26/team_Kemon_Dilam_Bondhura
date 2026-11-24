import {installedApp,nativeBuild} from './apiBase';
import { lazy, StrictMode, Suspense } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import {VerifyPassport} from './Passport';
import {ThemeProvider} from './Theme';
import {I18nProvider} from './i18n';
import './style.css';
const EmailUnsubscribe=lazy(()=>import('./EmailAlerts').then(module=>({default:module.EmailUnsubscribe})));
const CompetitionDemo=lazy(()=>import('./CompetitionDemo'));
const passport = /^\/verify\/([a-f0-9]{32})\/?$/.exec(location.pathname);
const demo = /^\/demo\/?$/.test(location.pathname);
const page = demo ? <Suspense fallback={<main/>}><CompetitionDemo/></Suspense>
  : location.pathname==='/bill-reminders/unsubscribe' ? <Suspense fallback={null}><EmailUnsubscribe/></Suspense>
  : passport ? <VerifyPassport id={passport[1]}/> : <App/>;
createRoot(document.getElementById('root')!).render(<StrictMode><ThemeProvider><I18nProvider>{page}</I18nProvider></ThemeProvider></StrictMode>);

if (!nativeBuild && import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}

if(installedApp)document.documentElement.classList.add('native-app');
if(nativeBuild){
  void import('./nativeLinks').then(module=>module.listenToNativeLinks());
}
