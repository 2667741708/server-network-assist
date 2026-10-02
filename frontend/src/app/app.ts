import { ChangeDetectorRef, Component, OnDestroy, OnInit, signal } from '@angular/core';
import { NavigationController } from './smooth-navigation';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { forkJoin } from 'rxjs';
import { ApiService } from './api.service';
import { AuditComponent } from './audit.component';
import { CodexChatComponent } from './codex-chat.component';
import { BrowserComponent } from './browser.component';
import { ClashComponent } from './clash.component';
import { CommercialComponent } from './commercial.component';
import { HostsComponent } from './hosts.component';
import { LoginComponent } from './login.component';
import {
  AuditEvent,
  Credential,
  Host,
  NetworkProfile,
  ProbeResult,
  SecurityInfo,
  SessionInfo,
} from './models';
import { NetworkComponent } from './network.component';
import { SettingsComponent } from './settings.component';
import { TerminalComponent } from './terminal.component';
import { CommercialSection } from './models';

@Component({
  imports: [
    MatButtonModule,
    MatProgressBarModule,
    LoginComponent,
    HostsComponent,
    NetworkComponent,
    TerminalComponent,
    SettingsComponent,
    AuditComponent,
    CodexChatComponent,
    BrowserComponent,
    ClashComponent,
    CommercialComponent,
  ],
  selector: 'app-root',
  styleUrl: './app.scss',
  templateUrl: './app.html',
})
export class App implements OnInit, OnDestroy {
  readonly session = signal<SessionInfo | null>(null);
  readonly page = signal('overview');
  readonly commercialVisited = signal(false);
  private navigation?: NavigationController;
  private scrollRestored = false;
  readonly busy = signal(true);
  readonly error = signal('');
  readonly hosts = signal<Host[]>([]);
  readonly credentials = signal<Credential[]>([]);
  readonly profiles = signal<NetworkProfile[]>([]);
  readonly probes = signal<ProbeResult[]>([]);
  readonly security = signal<SecurityInfo>({
    devices: [],
    passkeys: [],
    key_enabled: false,
    verified: false,
  });
  readonly audit = signal<AuditEvent[]>([]);
  readonly codexHost = signal('');
  readonly nav = [
    ['overview', '概览', '◫'],
    ['customers', '客户', '◎'],
    ['subscriptions', '订阅与套餐', '▣'],
    ['devices', '设备', '⌘'],
    ['nodes', '节点与出口', '↗'],
    ['leases', '租约', '◷'],
    ['directory', '目录发布', '◇'],
    ['audit', '审计', '≡'],
    ['settings', '系统设置', '⚙'],
  ];
  readonly toolNav = [
    ['hosts', '主机与凭据', '⌘'],
    ['network', '网络借助', '↗'],
    ['terminal', '终端', '>_'],
    ['codex', 'Codex 对话', '✦'],
    ['browser', '浏览器', '◎'],
    ['clash', '代理与 TUN', '◉'],
  ];
  readonly commercialSections = new Set<CommercialSection>([
    'overview',
    'customers',
    'subscriptions',
    'devices',
    'nodes',
    'leases',
    'directory',
  ]);
  readonly navigate = (value: string) => {
    if (!this.nav.some(item => item[0] === value) && !this.toolNav.some(item => item[0] === value)) return;
    if (this.navigation) void this.navigation.navigate(value);
    else this.commitNavigation(value);
  };
  readonly navigateCodex = (hostId: string) => {
    this.codexHost.set(hostId);
    this.navigate('codex');
  };
  private probesStarted = false;
  constructor(private readonly api: ApiService, private readonly changeDetector: ChangeDetectorRef) {}
  ngOnInit() {
    this.navigation = window.SmoothNavigation?.create({
      views:[...this.nav, ...this.toolNav].map(item => item[0]), initial:'overview',
      render:value => { this.commitNavigation(value); this.changeDetector.detectChanges(); this.markHeading(); },
      canNavigate:() => !!this.session()?.authenticated,
      prefetch:value => this.commercialSections.has(value as CommercialSection) ? this.api.prefetch('/api/client-service') : undefined,
      focusTarget:() => document.querySelector<HTMLElement>('.content-area'),
      onError:error => this.error.set(error.message)
    });
    const requested = this.navigation?.current || new URL(location.href).searchParams.get('view') || 'overview';
    if(this.nav.some(item => item[0] === requested)) this.page.set(requested);
    this.commercialVisited.set(this.isCommercialPage(this.page()));
    this.bootstrap();
  }
  ngOnDestroy() { this.navigation?.destroy(); }
  navigationHref(value: string) { return this.navigation?.url(value) || `?view=${encodeURIComponent(value)}`; }
  private markHeading() {
    document.querySelectorAll('[data-navigation-heading]').forEach(el => el.removeAttribute('data-navigation-heading'));
    document.querySelector('.content-area app-commercial:not([hidden]) h1, .content-area > :not([hidden]) h1')?.setAttribute('data-navigation-heading','');
  }
  private commitNavigation(value: string) {
    this.page.set(value);
    if(this.isCommercialPage(value)) this.commercialVisited.set(true);
    if (value === 'settings' || value === 'audit') this.refresh();
  }
  isCommercialPage(value: string): value is CommercialSection {
    return this.commercialSections.has(value as CommercialSection);
  }
  commercialSection(): CommercialSection {
    const value = this.page();
    return this.isCommercialPage(value) ? value : 'overview';
  }
  bootstrap() {
    this.busy.set(true);
    this.api.session().subscribe({
      next: (value) => {
        this.session.set(value);
        if (value.authenticated) { this.refresh(); setTimeout(() => {this.navigation?.observe();this.markHeading();}); }
        else this.busy.set(false);
      },
      error: (e) => {
        this.error.set(e.message);
        this.busy.set(false);
      },
    });
  }
  refresh(forceProbes = false) {
    this.busy.set(true);
    forkJoin({
      hosts: this.api.get<{ hosts: Host[] }>('/api/hosts'),
      credentials: this.api.get<{ credentials: Credential[] }>('/api/credentials'),
      network: this.api.get<{ profiles: NetworkProfile[] }>('/api/network'),
      security: this.api.get<SecurityInfo>('/api/security'),
      audit: this.api.get<{ events: AuditEvent[] }>('/api/audit'),
    }).subscribe({
      next: (value) => {
        this.hosts.set(value.hosts.hosts);
        this.credentials.set(value.credentials.credentials);
        this.profiles.set(value.network.profiles);
        this.security.set(value.security);
        this.audit.set(value.audit.events);
        this.busy.set(false);
        if(!this.scrollRestored){this.scrollRestored=true;this.navigation?.restoreScroll();}
        if (value.hosts.hosts.length && (forceProbes || !this.probesStarted)) {
          this.probesStarted = true;
          this.refreshProbes(value.hosts.hosts.map((host) => host.id));
        }
      },
      error: (e) => {
        if (e.message === '请先登录') {
          this.session.set({ ...this.session()!, authenticated: false });
        }
        this.error.set(e.message);
        this.busy.set(false);
      },
    });
  }
  refreshProbes(ids: string[]) {
    this.api.post<{ results: ProbeResult[]; profiles: NetworkProfile[] }>('/api/network/probe', { ids }).subscribe({
      next: (value) => { this.probes.set(value.results); this.profiles.set(value.profiles); },
      error: (e) => this.error.set(`服务器状态刷新失败：${e.message}`),
    });
  }
  logout() {
    this.api
      .post('/api/logout', {})
      .subscribe({ next: () => location.reload(), error: (e) => this.error.set(e.message) });
  }
}
