// Noah's model calls are OpenAI-style chat completions (noah/llm.py). WebLLM takes nearly the same request, with these gaps:
// - tool calling only works for a few 8B Hermes models: for every model, tools are emulated with JSON constrained to the
//   tools' schemas (WebLLM's grammar engine), and the JSON is turned back into `tool_calls`;
// - structured output is `{ type: 'json_object', schema: '<JSON text>' }` instead of OpenAI's `json_schema`;
// - at most 5 `top_logprobs`, and none at temperature 0; unknown fields (reasoning_effort, tool_choice, …) are refused, so only known ones are passed.

const PASS = ['messages', 'temperature', 'top_p', 'max_tokens', 'stop', 'seed', 'frequency_penalty', 'presence_penalty', 'n', 'logprobs', 'top_logprobs'];
const NO_ARGS = { type: 'object', properties: {} };

const argsSchema = f => (f.parameters?.properties && Object.keys(f.parameters.properties).length ? f.parameters : NO_ARGS);
const fields = f => JSON.stringify(argsSchema(f).properties);

function toolPlan(tools, choice) {
  const fns = tools.filter(t => t.type === 'function').map(t => t.function);
  const forced = fns.find(f => f.name === choice?.function?.name);
  if (forced) return {
    names: [forced.name], forced: forced.name, schema: argsSchema(forced),
    prompt: `Call the tool "${forced.name}" (${forced.description || 'no description'}) for the user's message. `
      + `Reply with its arguments only, as one JSON object with these fields: ${fields(forced)}.`,
  };
  return {
    names: fns.map(f => f.name), forced: '',
    schema: { anyOf: fns.map(f => ({ type: 'object', properties: { name: { enum: [f.name] }, arguments: argsSchema(f) }, required: ['name', 'arguments'] })) },
    prompt: 'You can call one of these tools:\n' + fns.map(f => `- ${f.name}: ${f.description || ''} Arguments: ${fields(f)}`).join('\n')
      + '\nPick the one tool that fits the user\'s message best and reply only with JSON: {"name": "<tool name>", "arguments": {...}}.',
  };
}

function withSystem(messages, text) {
  const [first, ...rest] = messages;
  return first?.role === 'system' ? [{ ...first, content: `${first.content}\n\n${text}` }, ...rest] : [{ role: 'system', content: text }, ...messages];
}

/** An OpenAI-style request -> { request: what WebLLM accepts, plan: how to read the reply (null unless tools are emulated) }. */
export function toWebLLM(body) {
  const request = Object.fromEntries(PASS.filter(k => body[k] !== undefined && body[k] !== null).map(k => [k, body[k]]));
  if (request.top_logprobs > 5) request.top_logprobs = 5;
  // logprobs come from the softmax at the request's temperature, so at 0 they are 0 for the pick and null for the rest:
  // temperature 1 keeps the model's real probabilities, and the smallest top_p still always picks the most likely token
  if (request.logprobs && !(request.temperature > 0)) Object.assign(request, { temperature: 1, top_p: 1e-5 });
  const rf = body.response_format;
  if (rf?.type === 'json_schema') request.response_format = { type: 'json_object', schema: JSON.stringify(rf.json_schema?.schema || {}) };
  else if (rf?.type === 'json_object') request.response_format = { type: 'json_object' };
  let plan = null;
  if (body.tools?.length && body.tool_choice !== 'none') {
    plan = toolPlan(body.tools, body.tool_choice);
    request.messages = withSystem(request.messages, plan.prompt);
    request.response_format = { type: 'json_object', schema: JSON.stringify(plan.schema) };
  }
  // Qwen3 models think before answering unless told not to; constrained JSON or a one-letter answer has no room for that
  if (/^Qwen3/i.test(body.model || '') && (request.response_format || body.reasoning_effort === 'none')) request.extra_body = { enable_thinking: false };
  return { request, plan };
}

/** WebLLM's reply -> the OpenAI-style reply Noah expects (emulated tool calls become `tool_calls`). */
export function fromWebLLM(reply, plan) {
  if (!plan) return reply;
  const choice = reply.choices?.[0];
  let call = null;
  try {
    const json = JSON.parse(choice.message.content);
    call = plan.forced ? { name: plan.forced, arguments: json } : plan.names.includes(json?.name) ? json : null;
  } catch { /* not JSON: leave the text, the caller falls back */ }
  if (!call) return reply;
  const message = { role: 'assistant', content: null,
    tool_calls: [{ id: 'call_0', type: 'function', function: { name: call.name, arguments: JSON.stringify(call.arguments ?? {}) } }] };
  return { ...reply, choices: [{ ...choice, message, finish_reason: 'tool_calls' }] };
}
