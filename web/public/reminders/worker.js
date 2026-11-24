self.addEventListener('push',event=>{
  event.waitUntil(self.registration.showNotification('Capzoryx',{
    body:'Open your workspace to review your updates.',tag:'capzoryx-updates'
  }));
});

self.addEventListener('notificationclick',event=>{
  event.notification.close();
  const destination=new URL('/?view=activity',self.location.origin).href;
  event.waitUntil(self.clients.openWindow(destination));
});
