// WebLLM's engine, off the page's thread: the model runs on the GPU (WebGPU) inside this worker, lib/webllm.js talks to it.
import { WebWorkerMLCEngineHandler } from '@mlc-ai/web-llm';

const handler = new WebWorkerMLCEngineHandler();
self.onmessage = msg => handler.onmessage(msg);
