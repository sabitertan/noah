export const send = (url, method, body) => fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
export const getJson = url => fetch(url).then(r => r.json());

export const store = {
  get(k, d) { try { const v = localStorage.getItem('noah.' + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('noah.' + k, JSON.stringify(v)); } catch (e) {} },
};
