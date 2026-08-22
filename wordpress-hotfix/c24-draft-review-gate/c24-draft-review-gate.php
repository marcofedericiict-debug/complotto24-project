<?php
/**
 * Plugin Name: Complotto24 Draft Review Gate
 * Description: Forza gli articoli importati dai batch Complotto24 in bozza e aggiunge una coda di revisione prima della pubblicazione.
 * Version: 0.1.0
 * Author: Complotto24
 * Requires at least: 6.0
 * Requires PHP: 8.0
 */

if (!defined('ABSPATH')) {
    exit;
}

const C24_DRG_VERSION = '0.1.0';
const C24_DRG_PENDING_META = '_c24_review_pending';
const C24_DRG_BATCH_META = '_c24_review_batch';

$GLOBALS['c24_drg_insert_count'] = 0;
$GLOBALS['c24_drg_batch_label'] = '';

function c24_drg_flatten_file_names($files): array {
    $out = [];
    if (!is_array($files)) {
        return $out;
    }
    foreach ($files as $value) {
        if (is_array($value)) {
            if (isset($value['name'])) {
                $names = $value['name'];
                if (is_array($names)) {
                    foreach ($names as $n) {
                        if (is_string($n)) $out[] = $n;
                    }
                } elseif (is_string($names)) {
                    $out[] = $names;
                }
            }
            $out = array_merge($out, c24_drg_flatten_file_names($value));
        }
    }
    return array_values(array_unique($out));
}

function c24_drg_uploaded_batch_name(): string {
    foreach (c24_drg_flatten_file_names($_FILES ?? []) as $name) {
        $base = sanitize_file_name(wp_basename($name));
        if (preg_match('/(?:complotto24|c24).*batch.*\.zip$/i', $base)) {
            return $base;
        }
    }
    return '';
}

function c24_drg_request_haystack(): string {
    $keys = ['page', 'action', 'task', 'tab', 'screen', 'importer'];
    $parts = [];
    foreach ($keys as $key) {
        if (isset($_REQUEST[$key]) && is_scalar($_REQUEST[$key])) {
            $parts[] = sanitize_text_field(wp_unslash((string) $_REQUEST[$key]));
        }
    }
    return strtolower(implode(' ', $parts));
}

function c24_drg_is_batch_import_request(): bool {
    if (!is_admin() || !current_user_can('edit_posts')) {
        return false;
    }

    $uploaded = c24_drg_uploaded_batch_name();
    if ($uploaded !== '') {
        $GLOBALS['c24_drg_batch_label'] = $uploaded;
        return true;
    }

    $hay = c24_drg_request_haystack();
    if ($hay !== '' && preg_match('/(?:(?:complotto24|c24).*(?:batch|import)|(?:batch|import).*(?:complotto24|c24))/i', $hay)) {
        return true;
    }

    $uid = get_current_user_id();
    if ($uid > 0 && get_transient('c24_drg_import_window_' . $uid)) {
        if ($hay !== '' && preg_match('/(?:batch|import|complotto24|c24)/i', $hay)) {
            return true;
        }
    }

    return false;
}

function c24_drg_arm_import_window(): void {
    $name = c24_drg_uploaded_batch_name();
    if ($name === '') return;
    $uid = get_current_user_id();
    if ($uid > 0) {
        set_transient('c24_drg_import_window_' . $uid, $name, 5 * MINUTE_IN_SECONDS);
    }
    $GLOBALS['c24_drg_batch_label'] = $name;
}
add_action('admin_init', 'c24_drg_arm_import_window', 1);

function c24_drg_force_draft($data, $postarr = [], $unsanitized_postarr = [], $update = false) {
    if (!c24_drg_is_batch_import_request()) {
        return $data;
    }
    if (!empty($update)) {
        return $data;
    }
    if (($data['post_type'] ?? 'post') !== 'post') {
        return $data;
    }

    $data['post_status'] = 'draft';
    return $data;
}
add_filter('wp_insert_post_data', 'c24_drg_force_draft', PHP_INT_MAX, 4);

function c24_drg_mark_review_post($post_id, $post, $update, $post_before = null): void {
    if ($update || !$post instanceof WP_Post) return;
    if ($post->post_type !== 'post' || $post->post_status !== 'draft') return;
    if (!c24_drg_is_batch_import_request()) return;

    $batch = $GLOBALS['c24_drg_batch_label'];
    if (!$batch) {
        $uid = get_current_user_id();
        $batch = $uid > 0 ? (string) get_transient('c24_drg_import_window_' . $uid) : '';
    }
    if (!$batch) {
        $batch = 'c24-batch-' . gmdate('Ymd-His');
    }

    update_post_meta($post_id, C24_DRG_PENDING_META, '1');
    update_post_meta($post_id, C24_DRG_BATCH_META, sanitize_text_field($batch));
    $GLOBALS['c24_drg_insert_count'] = (int) $GLOBALS['c24_drg_insert_count'] + 1;
}
add_action('wp_after_insert_post', 'c24_drg_mark_review_post', 20, 4);

function c24_drg_store_notice(): void {
    $count = (int) ($GLOBALS['c24_drg_insert_count'] ?? 0);
    $uid = get_current_user_id();
    if ($count > 0 && $uid > 0) {
        set_transient('c24_drg_notice_' . $uid, $count, 2 * MINUTE_IN_SECONDS);
    }
}
add_action('shutdown', 'c24_drg_store_notice');

function c24_drg_admin_notice(): void {
    $uid = get_current_user_id();
    if ($uid <= 0) return;
    $count = (int) get_transient('c24_drg_notice_' . $uid);
    if ($count <= 0) return;
    delete_transient('c24_drg_notice_' . $uid);
    $url = admin_url('edit.php?page=c24-draft-review');
    echo '<div class="notice notice-success is-dismissible"><p>';
    echo esc_html(sprintf('%d articoli Complotto24 importati come bozze. Nessun articolo e stato pubblicato automaticamente.', $count));
    echo ' <a href="' . esc_url($url) . '">Apri C24 Review</a></p></div>';
}
add_action('admin_notices', 'c24_drg_admin_notice');

function c24_drg_add_review_page(): void {
    add_submenu_page(
        'edit.php',
        'Complotto24 Review',
        'C24 Review',
        'edit_posts',
        'c24-draft-review',
        'c24_drg_render_review_page'
    );
}
add_action('admin_menu', 'c24_drg_add_review_page');

function c24_drg_render_review_page(): void {
    if (!current_user_can('edit_posts')) wp_die('Permessi insufficienti.');

    $query = new WP_Query([
        'post_type'      => 'post',
        'post_status'    => 'draft',
        'posts_per_page' => 100,
        'orderby'        => 'date',
        'order'          => 'DESC',
        'meta_key'       => C24_DRG_PENDING_META,
        'meta_value'     => '1',
    ]);

    $published = isset($_GET['c24_published']) ? absint($_GET['c24_published']) : 0;
    echo '<div class="wrap"><h1>Complotto24 Review</h1>';
    echo '<p>Questi articoli sono <strong>bozze non pubbliche</strong>. Apri Anteprima/Modifica e pubblica solo quelli approvati.</p>';
    if ($published > 0) {
        echo '<div class="notice notice-success"><p>' . esc_html(sprintf('%d articoli pubblicati.', $published)) . '</p></div>';
    }

    if (!$query->have_posts()) {
        echo '<div class="notice notice-info"><p>Nessun articolo Complotto24 in attesa di revisione.</p></div></div>';
        return;
    }

    echo '<form method="post" action="' . esc_url(admin_url('admin-post.php')) . '">';
    echo '<input type="hidden" name="action" value="c24_drg_publish">';
    wp_nonce_field('c24_drg_publish_selected', 'c24_drg_nonce');
    echo '<table class="widefat fixed striped"><thead><tr>';
    echo '<td class="check-column"><input type="checkbox" id="c24-select-all"></td><th>Titolo</th><th>Batch</th><th>Data</th><th>Controllo</th>';
    echo '</tr></thead><tbody>';

    foreach ($query->posts as $post) {
        $edit = get_edit_post_link($post->ID, '');
        $preview = get_preview_post_link($post);
        $batch = (string) get_post_meta($post->ID, C24_DRG_BATCH_META, true);
        echo '<tr>';
        echo '<th class="check-column"><input class="c24-review-check" type="checkbox" name="post_ids[]" value="' . esc_attr($post->ID) . '"></th>';
        echo '<td><strong>' . esc_html(get_the_title($post)) . '</strong></td>';
        echo '<td>' . esc_html($batch ?: 'batch Complotto24') . '</td>';
        echo '<td>' . esc_html(get_the_date('d/m/Y H:i', $post)) . '</td>';
        echo '<td><a class="button" target="_blank" rel="noopener" href="' . esc_url($preview) . '">Anteprima</a> ';
        echo '<a class="button" href="' . esc_url($edit) . '">Modifica</a></td>';
        echo '</tr>';
    }
    echo '</tbody></table>';
    echo '<p><button type="submit" class="button button-primary">Pubblica selezionati</button></p>';
    echo '</form>';
    echo '<script>document.getElementById("c24-select-all").addEventListener("change",function(){document.querySelectorAll(".c24-review-check").forEach(function(x){x.checked=event.target.checked;});});</script>';
    echo '</div>';
    wp_reset_postdata();
}

function c24_drg_publish_selected(): void {
    if (!current_user_can('publish_posts')) wp_die('Permessi insufficienti.');
    check_admin_referer('c24_drg_publish_selected', 'c24_drg_nonce');

    $ids = isset($_POST['post_ids']) && is_array($_POST['post_ids']) ? array_map('absint', $_POST['post_ids']) : [];
    $count = 0;
    foreach (array_unique(array_filter($ids)) as $post_id) {
        $post = get_post($post_id);
        if (!$post || $post->post_type !== 'post' || $post->post_status !== 'draft') continue;
        if (get_post_meta($post_id, C24_DRG_PENDING_META, true) !== '1') continue;
        $res = wp_update_post(['ID' => $post_id, 'post_status' => 'publish'], true);
        if (!is_wp_error($res)) {
            delete_post_meta($post_id, C24_DRG_PENDING_META);
            $count++;
        }
    }

    wp_safe_redirect(add_query_arg('c24_published', $count, admin_url('edit.php?page=c24-draft-review')));
    exit;
}
add_action('admin_post_c24_drg_publish', 'c24_drg_publish_selected');
