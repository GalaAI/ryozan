// Exact SVG paths extracted from approved DC, mapped across both the
// Ryōzanpaku Bandit Orchestra (soko, shishin, ensei, saishin, taiso,
// rochishin, andozen, riki) and legacy Ryozan keys.
export const sourceAgentMarks: Record<string, string> = {
  gala: '<path d="M12 1 15 9 23 12 15 15 12 23 9 15 1 12 9 9Z" fill="currentColor" stroke="none"/>',
  ensei: '<path d="M12 2 15 9 22 12 15 15 12 22 9 15 2 12 9 9Z"/>',
  bella: '<path d="M12 2 15 9 22 12 15 15 12 22 9 15 2 12 9 9Z"/>',
  saishin: '<circle cx="12" cy="12" r="9"/><path d="M12 3V12H21"/>',
  booky: '<circle cx="12" cy="12" r="9"/><path d="M12 3V12H21"/>',
  shishin: '<path d="m12 2 10 10-10 10L2 12Z"/><path d="m12 7 5 5-5 5-5-5Z" fill="currentColor" stroke="none"/>',
  xar: '<path d="m12 2 10 10-10 10L2 12Z"/><path d="m12 7 5 5-5 5-5-5Z" fill="currentColor" stroke="none"/>',
  taiso: '<path d="M12 3.2V20.8M3.2 12H20.8M5.8 5.8 18.2 18.2M18.2 5.8 5.8 18.2"/><circle cx="12" cy="12" r="3.2"/>',
  mercury: '<path d="M12 3.2V20.8M3.2 12H20.8M5.8 5.8 18.2 18.2M18.2 5.8 5.8 18.2"/><circle cx="12" cy="12" r="3.2"/>',
  soko: '<ellipse cx="12" cy="14.6" rx="3.1" ry="4.1"/><circle cx="12" cy="9" r="2.1"/><path d="M10.1 11.2 6.4 8.4 4.1 9.6M9.1 13.6 5.1 12.9 3 14.9M9.1 16.4 5.4 17.4 4.1 20M10.4 18.6 8.6 21 9.1 22.6M13.9 11.2 17.6 8.4 19.9 9.6M14.9 13.6 18.9 12.9 21 14.9M14.9 16.4 18.6 17.4 19.9 20M13.6 18.6 15.4 21 14.9 22.6M10.7 7.6 9.9 5.9M13.3 7.6 14.1 5.9"/>',
  spyder: '<ellipse cx="12" cy="14.6" rx="3.1" ry="4.1"/><circle cx="12" cy="9" r="2.1"/><path d="M10.1 11.2 6.4 8.4 4.1 9.6M9.1 13.6 5.1 12.9 3 14.9M9.1 16.4 5.4 17.4 4.1 20M10.4 18.6 8.6 21 9.1 22.6M13.9 11.2 17.6 8.4 19.9 9.6M14.9 13.6 18.9 12.9 21 14.9M14.9 16.4 18.6 17.4 19.9 20M13.6 18.6 15.4 21 14.9 22.6M10.7 7.6 9.9 5.9M13.3 7.6 14.1 5.9"/>',
  rochishin: '<circle cx="12" cy="12" r="8"/><path d="M12 4v16M4 12h16M6.3 6.3l11.4 11.4M17.7 6.3 6.3 17.7"/>',
  smoku: '<circle cx="12" cy="12" r="8"/><path d="M12 4v16M4 12h16M6.3 6.3l11.4 11.4M17.7 6.3 6.3 17.7"/>',
  andozen: '<path d="M12 4v16M4 12h16"/><rect x="6" y="6" width="12" height="12" rx="3"/>',
  docbot: '<path d="M12 4v16M4 12h16"/><rect x="6" y="6" width="12" height="12" rx="3"/>',
  doc: '<path d="M12 4v16M4 12h16"/><rect x="6" y="6" width="12" height="12" rx="3"/>',
  riki: '<path d="M4 18 19 5M19 5h-6M19 5v6M5 6l14 13"/>',
}
export const sourceIcons: Record<string,string> = {
  "mods": "<rect x=\"3\" y=\"3\" width=\"7\" height=\"7\" rx=\"1.5\"/><rect x=\"14\" y=\"3\" width=\"7\" height=\"7\" rx=\"1.5\"/><rect x=\"3\" y=\"14\" width=\"7\" height=\"7\" rx=\"1.5\"/><path d=\"M17.5 14v7M14 17.5h7\"/>",
  "home": "<rect x=\"3\" y=\"3\" width=\"7\" height=\"9\"/><rect x=\"14\" y=\"3\" width=\"7\" height=\"5\"/><rect x=\"14\" y=\"12\" width=\"7\" height=\"9\"/><rect x=\"3\" y=\"16\" width=\"7\" height=\"5\"/>",
  "house": "<path d=\"m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z\"/><polyline points=\"9 22 9 12 15 12 15 22\"/>",
  "bag": "<path d=\"M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z\"/><line x1=\"3\" y1=\"6\" x2=\"21\" y2=\"6\"/><path d=\"M16 10a4 4 0 0 1-8 0\"/>",
  "person": "<path d=\"M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2\"/><circle cx=\"12\" cy=\"7\" r=\"4\"/>",
  "books": "<path d=\"M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z\"/><path d=\"M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z\"/>",
  "studio": "<polygon points=\"13 2 3 14 12 14 11 22 21 10 12 10 13 2\"/>",
  "guild": "<path d=\"M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z\"/><path d=\"M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z\"/>",
  "courier": "<rect x=\"2\" y=\"4\" width=\"20\" height=\"16\" rx=\"2\"/><path d=\"m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7\"/>",
  "vault": "<path d=\"M21 12V7H5a2 2 0 0 1 0-4h14v4\"/><path d=\"M3 5v14a2 2 0 0 0 2 2h16v-5\"/><path d=\"M18 12a2 2 0 0 0 0 4h4v-4z\"/>",
  "cal": "<rect x=\"3\" y=\"4\" width=\"18\" height=\"18\" rx=\"2\"/><line x1=\"16\" y1=\"2\" x2=\"16\" y2=\"6\"/><line x1=\"8\" y1=\"2\" x2=\"8\" y2=\"6\"/><line x1=\"3\" y1=\"10\" x2=\"21\" y2=\"10\"/>",
  "heart": "<path d=\"M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z\"/>",
  "msg": "<path d=\"M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z\"/>",
  "toolkit": "<path d=\"M12 19l7-7 3 3-7 7-3-3z\"/><path d=\"M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z\"/><path d=\"M2 2l7.586 7.586\"/><circle cx=\"11\" cy=\"11\" r=\"2\"/>",
  "pit": "<circle cx=\"6\" cy=\"6\" r=\"3\"/><circle cx=\"18\" cy=\"6\" r=\"3\"/><circle cx=\"12\" cy=\"18\" r=\"3\"/><path d=\"M6 9v2a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V9\"/><path d=\"M12 15v-2\"/>"
}
