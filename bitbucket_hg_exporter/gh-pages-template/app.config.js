'use strict';

angular.
  module('BitbucketBackupApp').
  config(['$routeProvider',
    function config($routeProvider) {
      $routeProvider.
        when('/issues/page=:pageId', {
          template: '<issues-list></issues-list>'
        }).
        when('/issue/:issueId', {
          template: '<issue-details></issue-details>'
        }).
        otherwise('/issues/page=1');
    }
  ]);