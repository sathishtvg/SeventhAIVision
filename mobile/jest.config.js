/**
 * Jest config for the mobile app.
 *
 * jest-expo's preset handles the RN/Expo module graph (native modules, the
 * Flow-typed RN source, asset stubs). transformIgnorePatterns has to allow
 * node_modules through the babel transform because RN and the Expo packages
 * ship untranspiled ESM — the default "ignore all of node_modules" would
 * blow up on the first `import` statement inside react-native.
 *
 * moduleNameMapper mirrors the `@/*` alias that already exists in BOTH
 * tsconfig.json (for typecheck) and babel.config.js's module-resolver (for
 * the bundler). Jest reads neither, so without this third copy every
 * `import ... from '@/...'` in a test fails to resolve.
 */
module.exports = {
  preset: 'jest-expo',
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/src/$1',
  },
  setupFilesAfterEnv: ['<rootDir>/jest.setup.js'],
  transformIgnorePatterns: [
    'node_modules/(?!((jest-)?react-native|@react-native(-community)?)|expo(nent)?|@expo(nent)?/.*|@expo-google-fonts/.*|react-navigation|@react-navigation/.*|@unimodules/.*|unimodules|sentry-expo|native-base|react-native-svg)',
  ],
  collectCoverageFrom: [
    'src/**/*.{ts,tsx}',
    '!src/**/*.d.ts',
    '!src/theme/**',
  ],
  testMatch: ['<rootDir>/src/**/*.test.{ts,tsx}', '<rootDir>/__tests__/**/*.test.{ts,tsx}'],
}
