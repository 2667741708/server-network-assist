import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './app/App';
import './styles.css';

const root = document.getElementById('root');
if (!root) throw new Error('桌面 UI 根节点不存在');

createRoot(root).render(<StrictMode><App /></StrictMode>);
