"""
Main GUI implementation for Mahjong Copilot
The GUI is a desktop app based on tkinter library
GUI functions: controlling browser settings, displaying AI guidance info, game status
"""

import os
import time
import tkinter as tk
from tkinter import ttk, messagebox

from bot_manager import BotManager, mjai_reaction_2_guide
from common.utils import Folder, GameMode, GAME_MODES, GameClientType
from common.utils import UiState, sub_file, error_to_str
from common.log_helper import LOGGER, LogHelper
from common.settings import Settings
from common.mj_helper import GameInfo, MJAI_TILE_2_UNICODE
from updater import Updater, UpdateStatus
from .utils import GUI_STYLE, add_hover_text
from .settings_window import SettingsWindow
from .help_window import HelpWindow
from .widgets import *  # pylint: disable=wildcard-import, unused-wildcard-import


class MainGUI(tk.Tk):
    """ Main GUI Window"""
    def __init__(self, setting:Settings, bot_manager:BotManager):
        super().__init__()
        self.bot_manager = bot_manager
        self.st = setting
        # auto-close / auto-loop state (driven from the GUI update loop):
        self._pending_close_browser = False     # timer expired; close browser once the game is over
        self._game_over_since = None             # when is_in_game first went False (debounce)
        self._auto_loop_await_close = False      # waiting for the browser thread to fully stop
        self._auto_loop_relaunch_at = None       # timestamp to relaunch the browser (auto-loop)
        self.updater = Updater(self.st.update_url)
        self.after_idle(self.updater.load_help)
        self.after_idle(self.updater.check_update)        # check update when idle
        
        icon = tk.PhotoImage(file=sub_file(Folder.RES,'icon.png'))
        self.iconphoto(True, icon)
        self.protocol("WM_DELETE_WINDOW", self._on_exit)        # confirmation before close window  
        size = (620,540)      
        self.geometry(f"{size[0]}x{size[1]}")
        self.minsize(*size)
        # Styling
        scaling_factor = self.winfo_fpixels('1i') / 96
        GUI_STYLE.set_dpi_scaling(scaling_factor)
        style = ttk.Style(self)
        GUI_STYLE.set_style_normal(style)
        # icon resources:
        self.icon_green = sub_file(Folder.RES,'green.png')
        self.icon_red = sub_file(Folder.RES,'red.png')
        self.icon_yellow = sub_file(Folder.RES,'yellow.png')
        self.icon_gray =sub_file(Folder.RES,'gray.png')
        self.icon_ready = sub_file(Folder.RES,'ready.png')

        # create window widgets
        self._create_widgets()

        self.bot_manager.start()        # start the main program
        self.gui_update_delay = 50      # in ms
        self._update_gui_info()         # start updating gui info
        

    def _create_widgets(self):
        """ Create all widgets in the main window"""
        # Main window properties
        self.title(self.st.lan().APP_TITLE)        
        
        # container for grid control
        self.grid_frame = tk.Frame(self)
        self.grid_frame.pack(fill=tk.BOTH, expand=True)
        self.grid_frame.grid_columnconfigure(0, weight=1)
        grid_args = {'column':0, 'sticky': tk.EW, 'padx': 5, 'pady': 2}
        
        # === toolbar frame (row 0) ===
        cur_row = 0
        tb_ht = 70
        pack_args = {'side':tk.LEFT, 'padx':4, 'pady':4}
        self.toolbar = ToolBar(self.grid_frame, tb_ht)
        self.toolbar.grid(row=cur_row, **grid_args)
        self.grid_frame.grid_rowconfigure(cur_row, weight=0)
        
        # start game button
        self.toolbar.add_sep()
        self.btn_start_browser = self.toolbar.add_button(
            self.st.lan().START_BROWSER, 'majsoul.png', self._on_btn_start_browser_clicked)               
        # buttons on toolbar
        self.toolbar.add_sep()
        self.toolbar.add_button(self.st.lan().SETTINGS, 'settings.png', self._on_btn_settings_clicked)
        self.toolbar.add_button(self.st.lan().OPEN_LOG_FILE, 'log.png', self._on_btn_log_clicked)
        self.btn_help = self.toolbar.add_button(self.st.lan().HELP, 'help.png', self._on_btn_help_clicked)
        self.toolbar.add_sep()
        self.toolbar.add_button(self.st.lan().EXIT, 'exit.png', self._on_exit)
        
        # === 2nd toolbar ===
        cur_row += 1
        self.tb2 = ToolBar(self.grid_frame, tb_ht)
        self.tb2.grid(row=cur_row, **grid_args)
        sw_ft_sz = 10
        self.tb2.add_sep()
        # Switches
        self.switch_overlay = ToggleSwitch(
            self.tb2, self.st.lan().WEB_OVERLAY, tb_ht, font_size=sw_ft_sz, command=self._on_switch_hud_clicked)
        self.switch_overlay.pack(**pack_args)
        self.tb2.add_sep()
        self.switch_autoplay = ToggleSwitch(
            self.tb2, self.st.lan().AUTOPLAY, tb_ht, font_size=sw_ft_sz, command=self._on_switch_autoplay_clicked)
        self.switch_autoplay.pack(**pack_args)
        # game record
        self.tb2.add_sep()
        self.switch_record = ToggleSwitch(
            self.tb2, self.st.lan().GAME_RECORD, tb_ht, font_size=sw_ft_sz, command=self._on_switch_record_clicked)
        self.switch_record.pack(**pack_args)
        # auto join
        self.tb2.add_sep()
        self.switch_autojoin = ToggleSwitch(
            self.tb2, self.st.lan().AUTO_JOIN_GAME, tb_ht, font_size=sw_ft_sz, command=self._on_switch_autojoin_clicked)
        self.switch_autojoin.pack(**pack_args)
        # combo boxrd for auto join level and mode
        _frame = tk.Frame(self.tb2)
        _frame.pack(**pack_args)
        self.auto_join_level_var = tk.StringVar(value=self.st.lan().GAME_LEVELS[self.st.auto_join_level])
        options = self.st.lan().GAME_LEVELS
        combo_autojoin_level = ttk.Combobox(_frame, textvariable=self.auto_join_level_var, values=options, state="readonly", width=8)
        combo_autojoin_level.grid(row=0, column=0, padx=3, pady=3)   
        combo_autojoin_level.bind("<<ComboboxSelected>>", self._on_autojoin_level_selected)        
        mode_idx = GAME_MODES.index(self.st.auto_join_mode)
        self.auto_join_mode_var = tk.StringVar(value=self.st.lan().GAME_MODES[mode_idx])
        options = self.st.lan().GAME_MODES
        combo_autojoin_mode = ttk.Combobox(_frame, textvariable=self.auto_join_mode_var, values=options, state="readonly", width=8)
        combo_autojoin_mode.grid(row=1, column=0, padx=3, pady=3)
        combo_autojoin_mode.bind("<<ComboboxSelected>>", self._on_autojoin_mode_selected)
        # timer
        self.timer = Timer(self.tb2, tb_ht, sw_ft_sz, self.st.lan().AUTO_JOIN_TIMER)
        self.timer.set_callback(self._on_autojoin_timer_up)   # on time up: stop autojoin + close browser after game
        self.timer.pack(**pack_args)
        self.tb2.add_sep()
        # auto loop: after the timer ends and the game is over, relaunch the browser and restart
        # auto-join after an interval (minutes)
        self.switch_autoloop = ToggleSwitch(
            self.tb2, self.st.lan().AUTO_LOOP, tb_ht, font_size=sw_ft_sz, command=self._on_switch_autoloop_clicked)
        self.switch_autoloop.pack(**pack_args)
        _loop_frame = tk.Frame(self.tb2)
        _loop_frame.pack(**pack_args)
        _loop_lbl = tk.Label(_loop_frame, text=self.st.lan().AUTO_LOOP_INTERVAL, font=GUI_STYLE.font_normal(size=sw_ft_sz))
        _loop_lbl.grid(row=0, column=0, padx=1, pady=1)
        add_hover_text(_loop_lbl, self.st.lan().AUTO_LOOP_TIP)
        self.auto_loop_interval_var = tk.StringVar(value=f"{self.st.auto_loop_interval:g}")
        _loop_entry = tk.Entry(
            _loop_frame, textvariable=self.auto_loop_interval_var, width=5, justify=tk.CENTER,
            font=GUI_STYLE.font_normal(size=sw_ft_sz))
        _loop_entry.grid(row=1, column=0, padx=1, pady=1)
        _loop_entry.bind("<FocusOut>", self._on_auto_loop_interval_changed)
        _loop_entry.bind("<Return>", self._on_auto_loop_interval_changed)
        self.tb2.add_sep()
               
        # === AI guidance ===
        cur_row += 1
        _label = ttk.Label(self.grid_frame, text=self.st.lan().AI_OUTPUT)
        _label.grid(row=cur_row, **grid_args)
        self.grid_frame.grid_rowconfigure(cur_row, weight=0)
        
        cur_row += 1
        self.ai_guide_var = tk.StringVar()
        self.text_ai_guide = tk.Label(
            self.grid_frame,
            textvariable=self.ai_guide_var,
            font=GUI_STYLE.font_normal("Segoe UI Emoji",22),
            height=5, anchor=tk.NW, justify=tk.LEFT,
            relief=tk.SUNKEN, padx=5,pady=5,
            )
        self.text_ai_guide.grid(row=cur_row, **grid_args)
        self.grid_frame.grid_rowconfigure(cur_row, weight=1)        

        # === game info ===
        cur_row += 1
        _label = ttk.Label(self.grid_frame, text=self.st.lan().GAME_INFO)
        _label.grid(row=cur_row, **grid_args)
        self.grid_frame.grid_rowconfigure(cur_row, weight=0)
        cur_row += 1
        self.gameinfo_var = tk.StringVar()
        self.text_gameinfo = tk.Label(
            self.grid_frame,
            textvariable=self.gameinfo_var,
            height=2, anchor=tk.W, justify=tk.LEFT,
            font=GUI_STYLE.font_normal("Segoe UI Emoji",22),
            relief=tk.SUNKEN, padx=5,pady=5,
            )
        self.text_gameinfo.grid(row=cur_row, **grid_args)
        self.grid_frame.grid_rowconfigure(cur_row, weight=1)
        
        # === Model info ===
        cur_row += 1
        self.model_bar = StatusBar(self.grid_frame, 2)
        self.model_bar.grid(row=cur_row, column=0, sticky='ew', padx=1, pady=1)
        self.grid_frame.grid_rowconfigure(cur_row, weight=0)
        
        # === status bar ===
        # columns: 0 main thread | 1 game client | 2 dashboard URL | 3 status (expands)
        cur_row += 1
        self.status_bar = StatusBar(self.grid_frame, 4)
        self.status_bar.grid(row=cur_row, column=0, sticky='ew', padx=1, pady=1)
        self.grid_frame.grid_rowconfigure(cur_row, weight=0)
    
    def report_callback_exception(self, exc, val, tb):
        """ override exception handling: write to log"""
        LOGGER.error("GUI uncaught exception: %s", exc, exc_info=True)
        # super().report_callback_exception(exc, val, tb)
    
    def _on_autojoin_level_selected(self, _event):
        new_value = self.auto_join_level_var.get()    # convert to index
        self.st.auto_join_level = self.st.lan().GAME_LEVELS.index(new_value)
        
        
    def _on_autojoin_mode_selected(self, _event):
        new_mode = self.auto_join_mode_var.get()  # convert to string
        new_mode = self.st.lan().GAME_MODES.index(new_mode)
        new_mode = GAME_MODES[new_mode]
        self.st.auto_join_mode = new_mode
        

    def _on_btn_start_browser_clicked(self):
        self.btn_start_browser.config(state=tk.DISABLED)
        self.bot_manager.start_browser()
        

    def _on_switch_hud_clicked(self):
        self.switch_overlay.switch_mid()
        if not self.st.enable_overlay:
            self.bot_manager.enable_overlay()
        else:
            self.bot_manager.disable_overlay()
            
            
    def _on_switch_autoplay_clicked(self):
        self.switch_autoplay.switch_mid()
        if self.st.enable_automation:
            self.bot_manager.disable_automation()
        else:
            self.bot_manager.enable_automation()
            

    def _on_switch_record_clicked(self):
        self.switch_record.switch_mid()
        if self.st.enable_game_record:
            self.bot_manager.disable_game_record()
        else:
            self.bot_manager.enable_game_record()


    def _on_switch_autojoin_clicked(self):
        self.switch_autojoin.switch_mid()
        if self.st.auto_join_game:
            self.bot_manager.disable_autojoin()
        else:
            self.bot_manager.enable_autojoin()

    def _on_autojoin_timer_up(self):
        """ auto-join timer expired: stop auto-join, then close the browser once the game is over """
        LOGGER.info("Auto-join timer up: stopping auto-join; browser will close after the game ends")
        self.bot_manager.disable_autojoin()
        self._pending_close_browser = True

    def _on_switch_autoloop_clicked(self):
        self.switch_autoloop.switch_mid()
        self.st.enable_auto_loop = not self.st.enable_auto_loop
        self.st.save_json()
        if not self.st.enable_auto_loop:
            self._auto_loop_relaunch_at = None      # cancel any pending relaunch

    def _on_auto_loop_interval_changed(self, _event=None):
        try:
            val = float(self.auto_loop_interval_var.get())
            if 0 < val <= 1440:
                self.st.auto_loop_interval = val
                self.st.save_json()
        except ValueError:
            pass
        self.auto_loop_interval_var.set(f"{self.st.auto_loop_interval:g}")   # normalize/revert display

    # grace period the game must stay "over" before auto-close fires, so a transient
    # game_state gap (matchmaking/loading/brief disconnect) doesn't close the browser mid-session
    _AUTO_CLOSE_GRACE_SEC = 8.0

    def _update_auto_loop(self):
        """ Drive the auto-close / auto-loop state machine (called each GUI update tick):
        1) once the auto-join timer expired and the game has stayed over for a grace period, close
           the browser (revoked if the user re-enables auto-join);
        2) if Auto Loop is on, wait for the browser to fully stop, then after the interval relaunch
           it, re-enable auto-join, and restart the timer. """
        bm = self.bot_manager
        now = time.time()
        # 1) close the browser after the timer expired and the game has been over for the grace period
        if self._pending_close_browser:
            if self.st.auto_join_game:                      # user re-enabled auto-join -> keep playing
                self._pending_close_browser = False
                self._game_over_since = None
            elif not bm.browser.is_running():
                self._pending_close_browser = False         # nothing to close
                self._game_over_since = None
            elif bm.is_in_game():
                self._game_over_since = None                # still in a game; keep waiting
            else:                                           # not in a game -> debounce transient gaps
                if self._game_over_since is None:
                    self._game_over_since = now
                elif now - self._game_over_since >= self._AUTO_CLOSE_GRACE_SEC:
                    LOGGER.info("Auto-close: timer up and game over, closing browser")
                    bm.close_browser()
                    self._pending_close_browser = False
                    self._game_over_since = None
                    if self.st.enable_auto_loop:
                        self._auto_loop_await_close = True
        # 2) after closing, wait for the old browser thread to fully stop, then start the interval
        elif self._auto_loop_await_close:
            if not self.st.enable_auto_loop:
                self._auto_loop_await_close = False
            elif not bm.browser.is_running():               # old browser fully stopped
                self._auto_loop_await_close = False
                self._auto_loop_relaunch_at = now + self.st.auto_loop_interval * 60
                LOGGER.info("Auto-loop: browser will relaunch in %g min", self.st.auto_loop_interval)
        # 3) wait the interval, then relaunch (cancel if loop turned off or the user reopened it)
        elif self._auto_loop_relaunch_at is not None:
            if not self.st.enable_auto_loop or bm.browser.is_running():
                self._auto_loop_relaunch_at = None
            elif now >= self._auto_loop_relaunch_at:
                LOGGER.info("Auto-loop: relaunching browser and restarting auto-join")
                self._auto_loop_relaunch_at = None
                bm.start_browser()
                bm.enable_autojoin()
                self.timer.restart_saved()
            

    def _on_btn_log_clicked(self):
        # LOGGER.debug('Open log')
        os.startfile(LogHelper.log_file_name)
        

    def _on_btn_settings_clicked(self):
        # open settings dialog (modal/blocking)
        settings_window = SettingsWindow(self, self.st)
        settings_window.transient(self)
        settings_window.grab_set()
        self.wait_window(settings_window)
        
        if settings_window.exit_save:
            if settings_window.model_updated:
                self.bot_manager.set_bot_update()
            if settings_window.gui_need_reload:
                self.reload_gui()
            # mitm port occupy issue. Need to restart program for now
            # if settings_window.mitm_proxinject_updated:
                # message box to tell user to restart
                
            #     self.bot_manager.set_mitm_proxinject_update()
            

    def _on_btn_help_clicked(self):
        # open help dialog        
        help_win = HelpWindow(self, self.st, self.updater)
        help_win.transient(self)
        help_win.grab_set()
        
    
    def _on_exit(self):
        # Exit the app
        # pop up that confirm if the user really wants to quit
        if messagebox.askokcancel(self.st.lan().EXIT, self.st.lan().EIXT_CONFIRM, parent=self):
            try:
                LOGGER.info("Exiting GUI and program")
                self.status_bar.update_column(3, self.st.lan().EXIT + "ing...", self.icon_yellow)
                self.update_idletasks()
                self.st.save_json()
                self.bot_manager.stop(True)
            except: #pylint:disable=bare-except
                pass
            self.quit()
            
            
    def reload_gui(self):
        """ Clear UI compontes and rebuid widgets"""       
        for widget in self.winfo_children():
            widget.destroy()
        self._create_widgets()
        

    def _update_gui_info(self):
        """ Update GUI widgets status with latest info from bot manager"""
        try:
            self._update_gui_info_inner()
        except Exception as e:
            LOGGER.error("Error updating GUI: %s", e, exc_info=True)
        self.after(self.gui_update_delay, self._update_gui_info)
            
    def _update_gui_info_inner(self):
        """ Update GUI widgets status with latest info from bot manager"""
        # start browser button state
        if not self.bot_manager.browser.is_running():
            if self.bot_manager.get_game_client_type() == GameClientType.PROXY:
                self.btn_start_browser.config(state=tk.DISABLED)    # disable when proxy client running
            else:
                self.btn_start_browser.config(state=tk.NORMAL)
        else:
            self.btn_start_browser.config(state=tk.DISABLED)

        # help button
        if self.updater.update_status in (
            UpdateStatus.NEW_VERSION,
            UpdateStatus.DOWNLOADING,
            UpdateStatus.UNZIPPING,
            UpdateStatus.PREPARED
        ):
            self.toolbar.set_img(self.btn_help, 'help_update.png')
        else:
            self.toolbar.set_img(self.btn_help, 'help.png')
        
        # update switch states
        sw_list = [
            (self.switch_overlay, lambda: self.st.enable_overlay),
            (self.switch_autoplay, lambda: self.st.enable_automation),
            (self.switch_record, lambda: self.st.enable_game_record),
            (self.switch_autojoin, lambda: self.st.auto_join_game),
            (self.switch_autoloop, lambda: self.st.enable_auto_loop)
        ]
        for sw, func in sw_list:
            if func():
                sw.switch_on()
            else:
                sw.switch_off()

        # drive the auto-close / auto-loop state machine
        self._update_auto_loop()

        # Update AI guide from Reaction
        pending_reaction = self.bot_manager.get_pending_reaction()
        if pending_reaction:
            ai_guide_str, options = mjai_reaction_2_guide(pending_reaction, 3, self.st.lan())
            ai_guide_str += '\n'
            for tile_str, weight in options:
                ai_guide_str += f" {tile_str:8}  {weight*100:4.0f}%\n"
            self.ai_guide_var.set(ai_guide_str)
        else:
            self.ai_guide_var.set("")

        # update game info: display tehai + tsumohai
        gi:GameInfo = self.bot_manager.get_game_info()
        if gi and gi.my_tehai:
            tehai = gi.my_tehai
            tsumohai = gi.my_tsumohai
            hand_str = ''.join(MJAI_TILE_2_UNICODE[t] for t in tehai)
            if tsumohai:
                hand_str += f" + {MJAI_TILE_2_UNICODE[tsumohai]}"
            self.gameinfo_var.set(hand_str)
        else:
            self.gameinfo_var.set("")

        # bot/model info
        if self.bot_manager.is_bot_created():
            mode_strs = []
            for m in GameMode:
                if m in self.bot_manager.bot.supported_modes:
                    mode_strs.append('✔' + m.value)
                else:
                    mode_strs.append('✖' + m.value)
            mode_str = ' | '.join(mode_strs)
            text = f"{self.st.lan().MODEL}: {self.st.model_type} ({mode_str})"
            self.model_bar.update_column(0, text, self.icon_green)
            if self.bot_manager.is_game_syncing():
                self.model_bar.update_column(1, '⌛ ' + self.st.lan().SYNCING)
            elif self.bot_manager.is_bot_calculating():
                self.model_bar.update_column(1, '⌛ ' + self.st.lan().CALCULATING)
            else:
                self.model_bar.update_column(1, 'ℹ️' + self.bot_manager.bot.info_str)
        else:   # bot is not ready
            if self.bot_manager.is_loading_bot:
                text = self.st.lan().MODEL_LOADING
                icon = self.icon_yellow
            else:
                text = self.st.lan().MODEL_NOT_LOADED
                icon = self.icon_red
            self.model_bar.update_column(0, text, icon)
            self.model_bar.update_column(1, '')

        # Status bar
        # main thread
        fps_disp = min([999, self.bot_manager.fps_counter.fps])
        fps_str = f"({fps_disp:3.0f})"
        if self.bot_manager.is_running():       # main thread
            self.status_bar.update_column(0, self.st.lan().MAIN_THREAD + fps_str, self.icon_green)
        else:
            self.status_bar.update_column(0, self.st.lan().MAIN_THREAD + fps_str, self.icon_red)        

        # client/browser
        client_type = self.bot_manager.get_game_client_type()
        if client_type == GameClientType.PLAYWRIGHT:
            fps_disp = min(999, self.bot_manager.browser.fps_counter.fps)
            fps_str = f"({fps_disp:3.0f})"
            status_str = self.st.lan().BROWSER+fps_str
            if self.bot_manager.browser.is_running():
                icon = self.icon_green
            else:
                icon = self.icon_gray
        elif client_type == GameClientType.PROXY:
            status_str = self.st.lan().PROXY_CLIENT
            icon = self.icon_green
        else:
            status_str = self.st.lan().GAME_NOT_RUNNING
            icon = self.icon_ready
        self.status_bar.update_column(1, status_str, icon)

        # dashboard URL (shows the actually-bound port, which may differ from the configured one)
        dash_url = self.bot_manager.dashboard_url()
        if dash_url:
            self.status_bar.update_column(2, '🌐 ' + dash_url, self.icon_green)
        else:
            self.status_bar.update_column(2, '', self.icon_gray)

        # status (last col)
        status_str, icon = self._get_status_text_icon(gi)
        self.status_bar.update_column(3, status_str, icon)
        
        ### update overlay
        self.bot_manager.update_overlay()

    def _get_status_text_icon(self, gi:GameInfo) -> tuple[str, str]:
        # Get text and icon for status bar last column, based on bot running info
        # show info as : thread error > game error > game status
        bot_exception = self.bot_manager.main_thread_exception
        if bot_exception:
            return error_to_str(bot_exception, self.st.lan()), self.icon_red
        else:   # no exception in bot manager
            pass
        
        game_error:Exception = self.bot_manager.get_game_error()
        if game_error:
            return error_to_str(game_error, self.st.lan()), self.icon_red
        if self.bot_manager.is_browser_zoom_off():
            return self.st.lan().BROWSER_ZOOM_OFF, self.icon_red        
            
        if self.bot_manager.is_in_game():
            info_str = self.st.lan().GAME_RUNNING
            if self.bot_manager.is_game_syncing():
                info_str += " - " + self.st.lan().SYNCING
                return info_str, self.icon_green
            else:   # game in progress
                if gi and gi.bakaze:
                    info_str += ' '.join([
                        "", "-",
                        f"{self.st.lan().mjai2str(gi.bakaze)}",
                        f"{gi.kyoku} {self.st.lan().KYOKU}",
                        f"{gi.honba} {self.st.lan().HONBA}",
                    ])
                else:
                    info_str += " - " + self.st.lan().GAME_STARTING
                return info_str, self.icon_green
        else:
            state_dict = {
                UiState.MAIN_MENU: self.st.lan().MAIN_MENU,
                UiState.GAME_ENDING: self.st.lan().GAME_ENDING,
                UiState.NOT_RUNNING: self.st.lan().GAME_NOT_RUNNING,
            }
            info_str = self.st.lan().READY_FOR_GAME + " - " + state_dict.get(self.bot_manager.automation.ui_state, "")
            return info_str, self.icon_ready
