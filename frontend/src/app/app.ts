import { Component, OnInit, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { forkJoin } from 'rxjs';
import { ApiService } from './api.service';
import { AuditComponent } from './audit.component';
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
import { OverviewComponent } from './overview.component';
import { SettingsComponent } from './settings.component';
import { TerminalComponent } from './terminal.component';

@Component({
  imports: [
    MatButtonModule,
    MatProgressBarModule,
    LoginComponent,
    OverviewComponent,
    HostsComponent,
    NetworkComponent,
    TerminalComponent,
    SettingsComponent,
    AuditComponent,
  ],
  selector: 'app-root',
  styleUrl: './app.scss',
  templateUrl: './app.html',
})
export class App implements OnInit {
  readonly session = signal<SessionInfo | null>(null);
  readonly page = signal('overview');
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
  readonly nav = [
    ['overview', '总览', '◫'],
    ['hosts', '主机', '⌘'],
    ['network', '网络借助', '↗'],
    ['terminal', '终端', '>_'],
    ['settings', '安全', '◇'],
    ['audit', '审计', '≡'],
  ];
  readonly navigate = (value: string) => {
    this.page.set(value);
    if (value === 'settings' || value === 'audit') this.refresh();
  };
  constructor(private readonly api: ApiService) {}
  ngOnInit() {
    this.bootstrap();
  }
  bootstrap() {
    this.busy.set(true);
    this.api.session().subscribe({
      next: (value) => {
        this.session.set(value);
        if (value.authenticated) this.refresh();
        else this.busy.set(false);
      },
      error: (e) => {
        this.error.set(e.message);
        this.busy.set(false);
      },
    });
  }
  refresh() {
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
  logout() {
    this.api
      .post('/api/logout', {})
      .subscribe({ next: () => location.reload(), error: (e) => this.error.set(e.message) });
  }
}
