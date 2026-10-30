import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

// ── BTTCO Modern Classic adherence gate ─────────────────────────────
// Raw hex, raw px and off-system fonts in app code are errors. The design
// system owns every value: use var(--token) or the `token` export of
// @bttco/ui. The files in DS_LEGACY_DEBT predate the gate; remove a file
// from that list once it is token-clean. New files are gated immediately.
const DS_ADHERENCE_RULES = {
  'no-restricted-syntax': [
    'error',
    {
      selector: "Literal[value=/#[0-9a-fA-F]{3,8}\\b/]",
      message: 'Raw hex color. Use a design-system color token via var(--name).',
    },
    {
      selector: "TemplateElement[value.raw=/#[0-9a-fA-F]{3,8}\\b/]",
      message: 'Raw hex color. Use a design-system color token via var(--name).',
    },
    {
      selector: "Literal[value=/\\b\\d+px\\b/]",
      message: 'Raw px value. Use a design-system spacing token via var(--name).',
    },
    {
      selector: "Literal[value=/font-family\\s*:\\s*(?!['\"]?(?:Poppins|Space Grotesk|JetBrains Mono|Bebas Neue|KC Tallboy|KC Tallboy Inked|KC SimpleSans|KC SimpleSans Inked|Suti|Courier New|Courier|Oswald|Inter|IBM Plex Mono))/i]",
      message: 'Font not provided by the design system. Use var(--font-display|body|mono|title|heading|script|typewriter).',
    },
  ],
}
// TODO(ds): token-clean these files and delete them from this list.
const DS_LEGACY_DEBT = [
  'src/components/ui/**', // shadcn primitives; replaced by @bttco/ui over time
  'src/pages/dashboard/ClientsPage.tsx',
  'src/App.tsx',
  'src/components/ArtistScroll.tsx',
  'src/components/CountersignModal.tsx',
  'src/components/CountersignPanel.tsx',
  'src/components/DailyQuests.tsx',
  'src/components/DevModeBar.tsx',
  'src/components/EmailConnectQuest.tsx',
  'src/components/EmailFoundryFrame.tsx',
  'src/components/EmailTemplateEditor.tsx',
  'src/components/FlashPlacementPreview.tsx',
  'src/components/GalaPanel.tsx',
  'src/components/GalaPersonalitySlider.tsx',
  'src/components/InstallAppQuest.tsx',
  'src/components/LoginGate.tsx',
  'src/components/LogoLoader.tsx',
  'src/components/POSModal.tsx',
  'src/components/ParkingMap.tsx',
  'src/components/PortalIntermission.tsx',
  'src/components/PushOptIn.tsx',
  'src/components/ScrollsPanel.tsx',
  'src/components/SignaturePad.tsx',
  'src/components/SmsTemplateEditor.tsx',
  'src/components/Toast.tsx',
  'src/components/assets/AssetsPanel.tsx',
  'src/components/booking/DayPreference.tsx',
  'src/components/chat/MessageInput.tsx',
  'src/components/layout/DashboardLayout.tsx',
  'src/components/layout/ProtectedRoute.tsx',
  'src/pages/ArtistPage.tsx',
  'src/pages/BookingPage.tsx',
  'src/pages/HomePage.tsx',
  'src/pages/LegalPage.tsx',
  'src/pages/OurStoryPage.tsx',
  'src/pages/PortalPage.tsx',
  'src/pages/QuoteConfirmedPage.tsx',
  'src/pages/QuotePage.tsx',
  'src/pages/dashboard/AdminUsers.tsx',
  'src/pages/dashboard/ArtistProfileEditor.tsx',
  'src/pages/dashboard/AxiomPanel.tsx',
  'src/pages/dashboard/BookingsManager.tsx',
  'src/pages/dashboard/ClubPage.tsx',
  'src/pages/dashboard/CollectorPortal.tsx',
  'src/pages/dashboard/DocumentHub.tsx',
  'src/pages/dashboard/LootPage.tsx',
  'src/pages/dashboard/MobilePortalHome.tsx',
  'src/pages/dashboard/ProfilePage.tsx',
  'src/pages/dashboard/QuestsPage.tsx',
  'src/pages/dashboard/SettingsPage.tsx',
  'src/pages/dashboard/VaultPage.tsx',
  'src/pages/dashboard/WalletPage.tsx',
]

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_' },
      ],
      ...DS_ADHERENCE_RULES,
    },
  },
  {
    files: DS_LEGACY_DEBT,
    rules: { 'no-restricted-syntax': 'off' },
  },
])
