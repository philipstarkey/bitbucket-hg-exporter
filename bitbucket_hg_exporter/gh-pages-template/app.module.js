'use strict';

// Define the `phonecatApp` module
var app = angular.module('BitbucketBackupApp', [
  'ngRoute',
  'issuesList',
  'issueDetails',
  'indexPage',
  'sidebarLinks'
]);

app.run(function($rootScope, $http) {
  $rootScope.project_name = 'empty';
  $http.get('bitbucket_data/repositories/philipstarkey/qtutils.json').then(function(response) {
    $rootScope.project_name = response.data['name'];
    $rootScope.project_data = response.data;

    $rootScope.links = [
      {text: 'Home', url:'#!/'},
      {text: 'Issues', url:'#!/issues/page=1'},
    ];
  });


})