import type { DisplayProfileName } from "./types";

export interface DisplayProfile {
  backgroundTop: number;
  backgroundBottom: number;
  environmentIntensity: number;
  keyIntensity: number;
  fillIntensity: number;
  rimIntensity: number;
  headlightIntensity: number;
}

export const displayProfiles: Record<DisplayProfileName, DisplayProfile> = {
  cad: {
    backgroundTop: 0xe8eef5,
    backgroundBottom: 0xaebccc,
    environmentIntensity: 0.65,
    keyIntensity: 2.1,
    fillIntensity: 0.75,
    rimIntensity: 0.8,
    headlightIntensity: 0.18,
  },
  "nx-like": {
    backgroundTop: 0xdce3eb,
    backgroundBottom: 0x8798aa,
    environmentIntensity: 0.8,
    keyIntensity: 2.7,
    fillIntensity: 1.0,
    rimIntensity: 1.25,
    headlightIntensity: 0.24,
  },
  studio: {
    backgroundTop: 0x293544,
    backgroundBottom: 0x0e141b,
    environmentIntensity: 1.05,
    keyIntensity: 3.4,
    fillIntensity: 1.15,
    rimIntensity: 1.8,
    headlightIntensity: 0.1,
  },
};
