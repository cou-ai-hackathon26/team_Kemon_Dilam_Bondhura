// Shared by the service worker and the app. Each operation closes its database connection.
(() => {
  const database = 'capzoryx-capture-v1';
  function open() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(database, 1);
      request.onupgradeneeded = () => {
        const db = request.result;
        db.createObjectStore('identity');
        db.createObjectStore('drafts', {keyPath:'request_id'});
        db.createObjectStore('shares', {keyPath:'id'});
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }
  async function use(table, mode, action) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(table, mode);
      const result = action(tx.objectStore(table));
      tx.oncomplete = () => {db.close();resolve(result.result);};
      tx.onabort = () => {db.close();reject(tx.error || new Error('Storage unavailable'));};
    });
  }
  const scope = () => use('identity','readonly',s=>s.get('owner'));
  const same = (a,b) => !!a && !!b && a.business_id===b.business_id && a.user_id===b.user_id;
  // Bind identity and payload in a single transaction. Logout clears the same stores atomically.
  async function saveScoped(table, makeRow) {
    const db=await open();
    return new Promise((resolve,reject)=>{
      const tx=db.transaction(['identity',table],'readwrite');
      let validationError;
      const current=tx.objectStore('identity').get('owner');
      current.onsuccess=()=>{
        try{tx.objectStore(table).add(makeRow(current.result));}
        catch(error){validationError=error;tx.abort();}
      };
      tx.oncomplete=()=>{db.close();resolve();};
      tx.onabort=()=>{db.close();reject(validationError||tx.error);};
    });
  }
  self.capzoryxStore = {
    scope, same,
    setScope: owner => use('identity','readwrite',s=>owner?s.put(owner,'owner'):s.delete('owner')),
    saveDraft: draft => saveScoped('drafts',owner=>{
      if(!same(owner,draft)||owner.currency!==draft.currency)throw new Error('Workspace changed. Reopen your workspace.');
      return draft;
    }),
    drafts: async owner => (await use('drafts','readonly',s=>s.getAll())).filter(d=>same(owner,d)),
    removeDraft: id => use('drafts','readwrite',s=>s.delete(id)),
    async share(text) {
      if(typeof text!=='string'||!text.trim()||text.length>2000)throw new RangeError('Expected 1–2000 characters');
      const id = crypto.randomUUID();
      await saveScoped('shares',owner=>{
        if(owner?.role==='bookkeeper')throw new DOMException('An owner reviews shared payment text.','NotAllowedError');
        return {id,text,owner,created_at:Date.now()};
      });
      return id;
    },
    getShare: id => use('shares','readonly',s=>s.get(id)),
    removeShare: id => use('shares','readwrite',s=>s.delete(id)),
    async clear() {
      const db = await open();
      await new Promise((resolve,reject)=>{
        const tables=['drafts','shares','identity'];
        const tx=db.transaction(tables,'readwrite');
        for(const name of tables)tx.objectStore(name).clear();
        tx.oncomplete=()=>{db.close();resolve();};
        tx.onabort=()=>{db.close();reject(tx.error);};
      });
      if('caches' in self) {
        for(const key of await caches.keys())if(key.startsWith('capzoryx-'))await caches.delete(key);
      }
    }
  };
})();
