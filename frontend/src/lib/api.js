export const send = (url, method, body) => fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
export const getJson = url => fetch(url).then(r => r.json());
// Requests that may change or run an expert's Python carry this header: another web site cannot add it without a CORS
// preflight, which the server never answers, so only Noah's own page can make them (see server._code_edit_allowed).
export const codeFetch = (url, method = 'GET', body) => fetch(url, { method, headers: { 'Content-Type': 'application/json', 'X-Noah-Code': '1' },
  body: body === undefined ? undefined : JSON.stringify(body) });

export const store = {
  get(k, d) { try { const v = localStorage.getItem('noah.' + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('noah.' + k, JSON.stringify(v)); } catch (e) {} },
};
