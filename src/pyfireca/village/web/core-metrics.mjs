// A frame's state counts and mass-weighted remaining combustible fuel.
export function eventOccurredBy(time, seconds) {
  return Number.isFinite(time) && time >= 0 && time <= seconds + 1e-9;
}

// Initial fuel connectivity only; terrain, contact height and ignition are separate.
export function adjacentFuelCounts(initialFuel, width, height, row, col) {
  let face = 0, diagonal = 0;
  for (let dr=-1; dr<=1; dr++) for (let dc=-1; dc<=1; dc++) {
    const r=row+dr, c=col+dc;
    if ((!dr&&!dc) || r<0 || c<0 || r>=height || c>=width) continue;
    if (initialFuel[r*width+c] > 0) {
      if (dr&&dc) diagonal++; else face++;
    }
  }
  return {face, diagonal};
}

export function summarizeCoreFrame(states, fuelFractions, initialFuel, frame, cells) {
  const offset = frame * cells;
  let heating = 0, burnedOut = 0, remainingFuelKg = 0;
  for (let i = 0; i < cells; i++) {
    const state = states[offset + i];
    if (state === 2) heating++;
    if (state === 4) burnedOut++;
    if (initialFuel && state !== 254 && state !== 255) {
      remainingFuelKg += initialFuel[i] * fuelFractions[offset + i];
    }
  }
  return {heating, burnedOut, remainingFuelKg: initialFuel ? remainingFuelKg : null};
}
