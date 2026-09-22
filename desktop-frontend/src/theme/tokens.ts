import type { Theme } from '@fluentui/react-components';
import { webDarkTheme, webLightTheme } from '@fluentui/react-components';

const shared = {
  fontFamilyBase: '"Segoe UI", "Microsoft YaHei", system-ui, sans-serif',
  fontFamilyMonospace: '"Cascadia Code", "SFMono-Regular", Consolas, monospace',
  borderRadiusMedium: '6px',
  borderRadiusLarge: '8px',
  borderRadiusXLarge: '10px',
  shadow4: '0 1px 2px rgba(15, 23, 42, 0.04)',
  shadow8: '0 4px 12px rgba(15, 23, 42, 0.08)',
};

export const operationsLightTheme: Theme = {
  ...webLightTheme,
  ...shared,
  colorBrandForeground1: '#0f6cbd',
  colorBrandForeground2: '#115ea3',
  colorBrandBackground: '#0f6cbd',
  colorBrandBackgroundHover: '#115ea3',
  colorBrandBackgroundPressed: '#0c3b6f',
  colorNeutralBackground1: '#ffffff',
  colorNeutralBackground2: '#f5f7fa',
  colorNeutralBackground3: '#eef1f5',
  colorNeutralStroke2: '#d8dee8',
  colorNeutralForeground1: '#1f2937',
  colorNeutralForeground2: '#526071',
};

export const operationsDarkTheme: Theme = {
  ...webDarkTheme,
  ...shared,
  colorBrandForeground1: '#62b0f5',
  colorBrandForeground2: '#8ac7ff',
  colorBrandBackground: '#0f6cbd',
  colorBrandBackgroundHover: '#338dcc',
  colorNeutralBackground1: '#1b1f24',
  colorNeutralBackground2: '#14171a',
  colorNeutralBackground3: '#242a31',
  colorNeutralStroke2: '#3c4652',
  colorNeutralForeground1: '#f1f5f9',
  colorNeutralForeground2: '#b7c1cc',
};
