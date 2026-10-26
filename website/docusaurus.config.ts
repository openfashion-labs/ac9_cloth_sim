import fs from 'node:fs';
import path from 'node:path';
import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

// This runs in Node.js - Don't use client-side code here (browser APIs, JSX...)

// リポジトリ固有の値はここに集約する。姉妹リポジトリへ website/ をコピーして
// 使うときは、このオブジェクトだけ書き換えれば動く。
const SITE = {
  title: 'AC9 Cloth Sim',
  tagline: 'Blender add-on documentation',
  org: 'openfashion-labs',
  repo: 'ac9_cloth_sim',
};

const repoUrl = `https://github.com/${SITE.org}/${SITE.repo}`;

// ドキュメント本体 (docs/en, docs/ja) はリポジトリルートに置かれる
// (website/ の外)。まだ別の担当者が書いていない間は存在しないので、
// 無い間は docs プラグインを両方外して準備中ページ (src/pages/index.md)
// を "/" に出す。docs がある時は pages プラグインと競合しないよう外す。
const hasDocs = fs.existsSync(path.resolve(__dirname, '../docs/en'));
const hasDocsJa = fs.existsSync(path.resolve(__dirname, '../docs/ja'));

const config: Config = {
  title: SITE.title,
  tagline: SITE.tagline,
  favicon: 'img/favicon.ico',

  future: {
    v4: true, // Improve compatibility with the upcoming Docusaurus v4
  },

  url: `https://${SITE.org}.github.io`,
  baseUrl: `/${SITE.repo}/`,

  organizationName: SITE.org,
  projectName: SITE.repo,

  onBrokenLinks: 'throw',
  markdown: {
    // マニュアルの .md は通常の Markdown (CommonMark) として読む。既定の MDX は
    // `<!-- -->` (HTML コメント、screenshot プレースホルダで使われる) を
    // 構文エラーにするため
    format: 'md',
    hooks: {
      onBrokenMarkdownLinks: 'warn',
    },
  },

  // Docusaurus 自体の i18n は使わない。英語/日本語は別々の docs プラグイン
  // インスタンス (routeBasePath '/' と 'ja') で出す。
  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      {
        docs: hasDocs
          ? {
              path: '../docs/en',
              routeBasePath: '/',
              sidebarPath: './sidebars.ts',
              editUrl: `${repoUrl}/tree/main/docs/en/`,
            }
          : false,
        blog: false,
        // docs が無い間は src/pages/index.md ("/" の準備中ページ) を出すため
        // pages プラグインを有効にする。docs がある時は "/" の衝突を避けて外す
        pages: hasDocs ? false : {},
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  plugins: hasDocs && hasDocsJa
    ? [
        [
          '@docusaurus/plugin-content-docs',
          {
            id: 'ja',
            path: '../docs/ja',
            routeBasePath: 'ja',
            sidebarPath: './sidebars.ts',
            editUrl: `${repoUrl}/tree/main/docs/ja/`,
          },
        ],
      ]
    : [],

  themeConfig: {
    colorMode: {
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: SITE.title,
      items: [
        ...(hasDocs
          ? [
              {to: '/', label: 'English', position: 'left' as const},
              ...(hasDocsJa
                ? [{to: '/ja/', label: '日本語', position: 'left' as const}]
                : []),
            ]
          : []),
        {
          href: repoUrl,
          label: 'GitHub',
          position: 'right' as const,
        },
      ],
    },
    footer: {
      style: 'dark',
      copyright: `Copyright © ${new Date().getFullYear()} OpenFashion`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
