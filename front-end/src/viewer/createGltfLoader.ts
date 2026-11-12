/// <reference types="vite/client" />
import { LoadingManager } from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/examples/jsm/loaders/DRACOLoader.js';
import decoderJsUrl from 'three/examples/jsm/libs/draco/gltf/draco_decoder.js?url';
import decoderWasmUrl from 'three/examples/jsm/libs/draco/gltf/draco_decoder.wasm?url';
import decoderWrapperUrl from 'three/examples/jsm/libs/draco/gltf/draco_wasm_wrapper.js?url';

// Resolve DRACOLoader's fixed filenames to Vite's local, hashed assets in both builds.
// Give only the decoder this manager so model URLs keep their normal resolution.
export function createGltfLoader() {
  const decoderAssets: Record<string, string> = {
    'draco_decoder.js': decoderJsUrl,
    'draco_decoder.wasm': decoderWasmUrl,
    'draco_wasm_wrapper.js': decoderWrapperUrl,
  };
  const manager = new LoadingManager();
  manager.setURLModifier((url) => decoderAssets[url] ?? url);
  const dracoLoader = new DRACOLoader(manager);
  const loader = new GLTFLoader();
  loader.setDRACOLoader(dracoLoader);
  return { loader, dracoLoader };
}
